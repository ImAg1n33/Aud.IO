"""评估门禁测试 —— 证明「红灯」真的会响，且阈值不会被悄悄删掉。

动机（RFC-019 §2.E / §7.9 SC-5）：评估链路此前只出指标、不设判据，
指标掉到多少都不会让 CI 变红，等于"有仪表盘没告警"。本文件覆盖三层：

1. 运行时端到端：真的 spawn 一次 intent eval，断言超阈值 → exit 1、达标 → exit 0。
   只断言函数返回值不够 —— 退出码才是 CI 真正读的东西。
2. 门禁函数单测：让 retrieval 的判据可以脱离模型下载被验证（纯函数）。
3. 防漂移静态断言：读 workflow YAML 纯文本，确认阈值还在。
   防止有人悄悄删掉 --fail-below → 门禁变成永不响的假门禁
   （这正是本项目刚修过的 N1/N22 同类病根：保护机制存在但从未被触发）。
4. YAML 可解析性 + 结构完整性：见 TestWorkflowYamlValidity。

⚠️ 第 3 层是纯文本断言，**看不见 YAML 失效** —— 本改动自己就踩了这个坑：
step 名里出现「冒号+空格」让整条 ci.yml 无法解析（GitHub 不报错，只是整个
workflow 一个 step 都不跑），而第 3 层断言依然全绿。所以必须有第 4 层。
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# eval/ 是仓库内包目录（非标准库模块），需确保仓库根在 sys.path 上。
# pytest.ini 已配置 pythonpath = .，这里是防御性兜底（直接 import 该模块时同样可用）。
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.run_retrieval_eval import _exit_code_for_gate  # noqa: E402


def _run_intent_eval(fail_below: str, report_path: Path) -> subprocess.CompletedProcess[str]:
    """以给定阈值跑一次 keyword 模式的意图评估，返回完成的进程对象。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "eval.run_intent_eval",
            "--mode",
            "keyword",
            "--fail-below",
            fail_below,
            "--output",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _read_workflow(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


class TestIntentEvalGateRuntime:
    """端到端：退出码才是 CI 真正读的东西（RFC §7.9 SC-5）。"""

    def test_gate_fires_when_accuracy_below_threshold(self, tmp_path: Path) -> None:
        proc = _run_intent_eval("0.99", tmp_path / "gate-fire.md")
        assert proc.returncode == 1, (
            f"阈值 0.99 应触发红灯，实际退出码 {proc.returncode}；"
            f"python={sys.executable}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert "[FAIL]" in proc.stdout, (
            f"红灯时应打印 [FAIL] 提示；python={sys.executable}\nstdout:\n{proc.stdout}"
        )

    def test_gate_passes_when_accuracy_above_threshold(self, tmp_path: Path) -> None:
        proc = _run_intent_eval("0.50", tmp_path / "gate-pass.md")
        assert proc.returncode == 0, (
            f"阈值 0.50 应放行，实际退出码 {proc.returncode}；"
            f"python={sys.executable}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


class TestRetrievalGateFunction:
    """门禁判据纯函数 —— 不下载模型即可验证。"""

    def test_no_threshold_always_passes(self) -> None:
        assert _exit_code_for_gate(0.644, None) == 0

    def test_above_threshold_passes(self) -> None:
        # BGE 实测 0.644 > 阈值 0.55
        assert _exit_code_for_gate(0.644, 0.55) == 0

    def test_below_threshold_fails(self) -> None:
        # MiniLM 实测 0.345 < 阈值 0.55 —— 灾难性回归必须被拦住
        assert _exit_code_for_gate(0.345, 0.55) == 1

    def test_equal_to_threshold_passes(self) -> None:
        # 等于阈值不算失败：阈值是"低于"，不是"不高于"
        assert _exit_code_for_gate(0.55, 0.55) == 0


class TestWorkflowGateDrift:
    """防漂移：阈值被删掉 = 门禁永不响 = 假门禁。"""

    def test_ci_intent_gate_threshold_present(self) -> None:
        text = _read_workflow("ci.yml")
        assert "--fail-below 0.80" in text, (
            "ci.yml 缺少 `--fail-below 0.80`：意图评估门禁已退化成只出指标"
        )

    def test_ci_retrieval_gate_smoke_present(self) -> None:
        text = _read_workflow("ci.yml")
        assert 'grep -q -- "--fail-below"' in text, (
            "ci.yml 缺少 retrieval 门禁接口冒烟：--fail-below 被删时 weekly 任务会直接崩"
        )

    def test_retrieval_workflow_gate_config_present(self) -> None:
        text = _read_workflow("eval-retrieval.yml")
        assert "EMBEDDING_PROVIDER: fastembed" in text, (
            "eval-retrieval.yml 未指定 EMBEDDING_PROVIDER=fastembed："
            "默认 local(MiniLM) MRR=0.345 < 0.55 会每周恒红"
        )
        assert "--fail-below 0.55" in text, (
            "eval-retrieval.yml 缺少 `--fail-below 0.55`：检索门禁已退化成只出指标"
        )


class TestWorkflowYamlValidity:
    """YAML 必须能解析，且结构没丢 —— 这是纯文本断言看不见的一层。

    实证事故（本改动自己造成，第一版 ci.yml 就是这么挂的）：
        - name: Smoke: retrieval eval gate interface intact
    未加引号的 plain scalar 里含「冒号+空格」→ YAML 解析失败 → GitHub 不报红，
    **整个 workflow 一个 step 都不跑**，连原有的 ruff / pytest / secret scan
    一起静默停摆。而上层的纯文本断言（TestWorkflowGateDrift）此时依然全绿。
    """

    def test_every_workflow_file_parses(self) -> None:
        files = sorted(WORKFLOW_DIR.glob("*.yml"))
        assert files, f"未找到任何 workflow 文件：{WORKFLOW_DIR}"
        for path in files:
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                pytest.fail(
                    f"{path.name} 不是合法 YAML —— 整个 workflow 会静默停摆：{exc}"
                )

    def test_ci_test_job_keeps_core_steps(self) -> None:
        """YAML 合法 ≠ 结构没丢：防止新增 step 时把老 step 挤掉。"""
        # 注意：PyYAML 走 YAML 1.1，顶层 `on` 会被解析成布尔 True，取值要兼容。
        data = yaml.safe_load(_read_workflow("ci.yml"))
        steps = data["jobs"]["test"]["steps"]
        names = [s.get("name") for s in steps]
        for required in ("Lint (ruff)", "Run tests", "Run secret scan"):
            assert required in names, f"ci.yml 的 test job 丢了关键 step：{required}"
