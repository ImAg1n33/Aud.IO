"""依赖声明护栏 —— 防止「无上界依赖」再次悄悄混进 requirements.txt。

动机（RFC-021 / N19）：2026-10-19 实测干净环境解析，`chromadb` 已从 0.5.x 漂到
**1.5.9**、`cachetools` 从 5.x 漂到 **7.2.3**、`starlette` 已到 1.9.0 —— 而当时
10 个直接依赖里**只有 2 个有上界**（pydantic / mcp）。

加上界不难，**难的是不让下一个人再加一行无上界的依赖**。这与 N1 / N22 是同一类
病根：保护机制存在但从未被触发。所以必须有测试，且要能被证伪。

不新增依赖：用 `packaging`（pip 自带，pydantic / chromadb 均已依赖）。

覆盖范围：只管**直接依赖**。传递依赖（numpy / onnxruntime / tokenizers）由上游
各自约束，替上游 pin 会在其正常升级时制造人为冲突（RFC-021 §2.3）。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"

_LOWER_OPS = (">=", ">", "==", "~=")
_UPPER_OPS = ("<", "<=", "===")


def _direct_requirements() -> list[Requirement]:
    """解析 requirements.txt，返回直接依赖（跳过注释、-r 等指令行）。"""
    reqs: list[Requirement] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        reqs.append(Requirement(line))
    return reqs


def _bounds(req: Requirement) -> tuple[Version | None, Version | None]:
    lo: Version | None = None
    hi: Version | None = None
    for spec in req.specifier:
        try:
            v = Version(spec.version)
        except InvalidVersion:  # pragma: no cover - 防御性
            continue
        if spec.operator in _UPPER_OPS:
            hi = v if hi is None else min(hi, v)
        elif spec.operator in _LOWER_OPS:
            lo = v if lo is None else max(lo, v)
    return lo, hi


class TestUpperBoundPresent:
    """T-RB-1 / T-RB-2：上界必须存在，且 chromadb 的取值不能被放宽。"""

    def test_every_direct_dependency_has_upper_bound(self) -> None:
        missing = [
            str(req)
            for req in _direct_requirements()
            if not any(s.operator in _UPPER_OPS for s in req.specifier)
        ]
        assert not missing, (
            "以下直接依赖没有上界，未来 major 版本破损无法被拦住：\n  "
            + "\n  ".join(missing)
            + "\n\n约定见 backend/requirements.txt 顶部注释：上界 = 当前主版本 + 1。"
        )

    def test_chromadb_upper_bound_is_major_two(self) -> None:
        chroma = [r for r in _direct_requirements() if r.name.lower() == "chromadb"]
        assert chroma, "requirements.txt 里找不到 chromadb"
        spec = chroma[0].specifier
        upper = [s for s in spec if s.operator in _UPPER_OPS]
        assert upper, "chromadb 丢了上界（N19 的原始缺陷）"
        assert Version(upper[0].version) <= Version("2.0"), (
            f"chromadb 上界被放宽到 {upper[0].version}：0.5.x 建的数据目录跨大版本需回归，"
            "放宽前必须先在真实数据上验证"
        )


class TestDeclarationSanity:
    """T-RB-3 / T-RB-5：声明本身要能解析，且区间可满足。"""

    def test_all_lines_parse_as_requirements(self) -> None:
        reqs = _direct_requirements()
        assert len(reqs) >= 10, f"直接依赖数量异常（{len(reqs)}），解析规则可能漏行"
        assert all(isinstance(r, Requirement) for r in reqs)

    def test_no_unsatisfiable_interval(self) -> None:
        bad = []
        for req in _direct_requirements():
            lo, hi = _bounds(req)
            if lo is not None and hi is not None and lo >= hi:
                bad.append(str(req))
        assert not bad, f"区间不可满足（下界 ≥ 上界）：{bad}"


class TestInstalledSatisfiesDeclaration:
    """T-RB-4：声明不能与实测环境脱节 —— 本机装的版本必须落在自己写的区间里。"""

    @pytest.mark.parametrize("req", _direct_requirements(), ids=lambda r: r.name)
    def test_installed_version_within_specifier(self, req: Requirement) -> None:
        try:
            installed = version(req.name)
        except PackageNotFoundError:
            pytest.skip(f"{req.name} 未安装在当前环境")
        assert Version(installed) in req.specifier, (
            f"{req.name} 实际安装 {installed}，不满足声明 {req.specifier}。"
            "要么修声明，要么说明为何允许脱节 —— 不要直接放宽上界绕过。"
        )
