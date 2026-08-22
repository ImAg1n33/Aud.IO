"""意图分类评估 runner —— 基于 eval/golden_intents.json 的基准测试。

用法（仓库根目录）:
    # 生产路径（Hard Signal 短路 → LLM → 关键词兜底），需要 LLM_API_KEY
    python -m eval.run_intent_eval --mode hybrid

    # 纯关键词层（确定性、无 LLM、CI 安全）
    python -m eval.run_intent_eval --mode keyword

    # 纯 LLM 层（度量模型本身，60 次调用，较慢）
    python -m eval.run_intent_eval --mode llm

参数:
    --mode        hybrid | keyword | llm        (默认 hybrid)
    --limit N     只评估前 N 条（快速冒烟）
    --fail-below  准确率低于阈值时退出码非 0（CI 门禁用）
    --output      Markdown 报告路径 (默认 eval/reports/intent-report.md)

输出: 控制台汇总 + Markdown 报告（总分、按意图混淆矩阵、按类别准确率、错分清单）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent.intent_classifier import Intent, IntentClassifier  # noqa: E402

VALID_INTENTS = {"music_play", "music_recommend", "weather", "chitchat", "unknown"}

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_intents.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "reports" / "intent-report.md"


def load_golden() -> list[dict[str, Any]]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    for case in cases:
        if case["intent"] not in VALID_INTENTS:
            raise ValueError(f"Invalid intent label in golden set: {case['intent']!r}")
    return cases


def _to_label(value: Any) -> str:
    if isinstance(value, Intent):
        return value.value
    return str(value)


async def classify_case(classifier: IntentClassifier, mode: str, text: str) -> str:
    """Return the predicted label, or 'error' if the classifier raised."""
    try:
        if mode == "keyword":
            return _to_label(classifier.classify(text))
        if mode == "llm":
            # 原始 LLM 路径（私有方法仅评估工具使用——度量模型本身，不含短路与兜底）
            return _to_label(await classifier._classify_via_llm(text))
        return _to_label(await classifier.classify_async(text))
    except Exception:
        return "error"


async def run_eval(mode: str, limit: int | None) -> list[dict[str, Any]]:
    classifier = IntentClassifier()
    cases = load_golden()
    if limit:
        cases = cases[:limit]

    results: list[dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        predicted = await classify_case(classifier, mode, case["input"])
        results.append(
            {
                "input": case["input"],
                "expected": case["intent"],
                "predicted": predicted,
                "category": case.get("category", "-"),
                "ok": predicted == case["intent"],
            }
        )
        print(f"[{idx}/{len(cases)}] {case['input'][:24]:<26} → {predicted:<16} expected={case['intent']} {'✓' if predicted == case['intent'] else '✗'}")

    return results


def _build_report(results: list[dict[str, Any]], mode: str, golden_path: str) -> str:
    total = len(results)
    correct = sum(1 for r in results if r["ok"])
    accuracy = correct / total if total else 0.0

    # 按意图的混淆矩阵
    intents = sorted(VALID_INTENTS) + ["error"]
    conf: dict[str, dict[str, int]] = {exp: {p: 0 for p in intents} for exp in intents}
    for r in results:
        conf[r["expected"]][r["predicted"]] += 1

    lines: list[str] = []
    lines.append("# 意图分类评估报告")
    lines.append("")
    lines.append(f"- 模式: `{mode}`")
    lines.append(f"- 用例数: {total} | 正确: {correct} | **准确率: {accuracy:.1%}**")
    lines.append(f"- Golden Set: `{golden_path}`")
    lines.append("")
    lines.append("## 混淆矩阵（行=期望，列=预测）")
    lines.append("")
    lines.append("| 期望 \\ 预测 | " + " | ".join(intents) + " | 行准确率 |")
    lines.append("|---|" + "---|" * len(intents) + "---|")
    for exp in intents:
        row = conf[exp]
        row_total = sum(row.values())
        row_acc = row[exp] / row_total if row_total else 0.0
        cells = " | ".join(str(row[p]) for p in intents)
        lines.append(f"| {exp} | {cells} | {row_acc:.0%} |")

    # 按类别准确率
    lines.append("")
    lines.append("## 按类别准确率")
    lines.append("")
    lines.append("| 类别 | 用例 | 正确 | 准确率 |")
    lines.append("|------|------|------|--------|")
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    for cat in sorted(by_cat):
        cat_ok = sum(1 for r in by_cat[cat] if r["ok"])
        lines.append(f"| {cat} | {len(by_cat[cat])} | {cat_ok} | {cat_ok / len(by_cat[cat]):.0%} |")

    # 错分清单
    misses = [r for r in results if not r["ok"]]
    lines.append("")
    lines.append(f"## 错分清单（{len(misses)}）")
    lines.append("")
    if misses:
        lines.append("| 输入 | 期望 | 预测 | 类别 |")
        lines.append("|------|------|------|------|")
        for r in misses:
            lines.append(f"| {r['input']} | {r['expected']} | {r['predicted']} | {r['category']} |")
    else:
        lines.append("无。")

    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Aud.IO 意图分类评估")
    parser.add_argument("--mode", choices=["hybrid", "keyword", "llm"], default="hybrid")
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条")
    parser.add_argument("--fail-below", type=float, default=None, help="准确率低于此值退出码非 0")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    results = await run_eval(args.mode, args.limit)
    report = _build_report(results, args.mode, str(GOLDEN_PATH))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")

    correct = sum(1 for r in results if r["ok"])
    accuracy = correct / len(results) if results else 0.0
    print(f"\n准确率 ({args.mode}): {accuracy:.1%} ({correct}/{len(results)})")
    print(f"报告已写入: {args.output}")

    if args.fail_below is not None and accuracy < args.fail_below:
        print(f"[FAIL] 准确率 {accuracy:.1%} 低于阈值 {args.fail_below:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))