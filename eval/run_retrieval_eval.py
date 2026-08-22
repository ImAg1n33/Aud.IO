"""记忆检索评估 runner —— 基于 eval/golden_retrieval.json 的语义召回基准。

用法（仓库根目录）:
    # 默认本地 ONNX 嵌入（与生产一致；首次运行会下载 ~80MB 模型）
    python -m eval.run_retrieval_eval

    # 对比 API 嵌入（需 LLM_API_KEY 且支持 /embeddings）
    python -m eval.run_retrieval_eval --embedding api

参数:
    --limit       检索 Top-K (默认 5)
    --embedding   local | api (默认 local)
    --output      Markdown 报告路径 (默认 eval/reports/retrieval-report.md)
    --keep-data   保留临时数据目录（调试用）

输出: 控制台逐查询命中 + Markdown 报告（recall@k、MRR、嵌入提供者）。

基线口径: 合成快照 + 人工标注期望命中，衡量 query_by_semantic 的召回质量。
升级嵌入模型或检索策略后重跑本基准即可量化对比（如 BGE-m3 vs MiniLM）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.memory.embedding import APIEmbedding, create_embedding_provider  # noqa: E402
from backend.memory.episodic_memory import EpisodicMemory  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_retrieval.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "reports" / "retrieval-report.md"


def load_golden() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data["seeds"], data["queries"]


def _normalise_expect(expect: Any) -> list[int]:
    if isinstance(expect, list):
        return [int(v) for v in expect]
    return [int(expect)]


async def seed_memory(memory: EpisodicMemory, seeds: list[dict[str, Any]]) -> None:
    for seed in seeds:
        # time_of_day 由系统时钟自动写入（store_snapshot 不接受该参数）
        await memory.store_snapshot(
            user_input=seed["user_input"],
            assistant_reply=seed["assistant_reply"],
            played_song={"name": seed["song_name"], "artist": seed["song_artist"]},
            mood_tag=seed.get("mood_tag"),
            session_id="default",
        )


async def run_eval(
    seeds: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    limit: int,
    embedding: str,
    keep_data: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="audio_eval_"))
    provider = APIEmbedding() if embedding == "api" else create_embedding_provider()
    memory = EpisodicMemory(db_path=tmp_dir / "episodes.db", embedding_provider=provider)

    try:
        await seed_memory(memory, seeds)

        results: list[dict[str, Any]] = []
        for idx, q in enumerate(queries, start=1):
            expected = _normalise_expect(q["expect"])
            snaps = await memory.query_by_semantic(q["query"], session_id="default", limit=limit)
            ids = [s.id for s in snaps]
            hit_rank = next(
                (rank for rank, sid in enumerate(ids, start=1) if sid in expected), None,
            )
            results.append(
                {
                    "query": q["query"],
                    "expected": expected,
                    "top_ids": ids,
                    "hit": hit_rank is not None,
                    "mrr": 1.0 / hit_rank if hit_rank else 0.0,
                    "note": q.get("note", ""),
                }
            )
            mark = "✓" if hit_rank else "✗"
            print(f"[{idx}/{len(queries)}] {q['query'][:22]:<26} top={ids} expected={expected} {mark}")

        n = len(results)
        hits = sum(1 for r in results if r["hit"])
        summary = {
            "queries": n,
            "hits": hits,
            "recall_at_k": hits / n if n else 0.0,
            "mrr": sum(r["mrr"] for r in results) / n if n else 0.0,
            "embedding": type(provider).__name__,
        }
        return results, summary
    finally:
        if not keep_data:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_report(results: list[dict[str, Any]], summary: dict[str, Any], limit: int) -> str:
    lines: list[str] = []
    lines.append("# 记忆检索评估报告")
    lines.append("")
    lines.append(f"- 嵌入提供者: `{summary['embedding']}` | Top-{limit}")
    lines.append(
        f"- 查询数: {summary['queries']} | 命中: {summary['hits']} | "
        f"**recall@{limit}: {summary['recall_at_k']:.1%}** | **MRR: {summary['mrr']:.3f}**"
    )
    lines.append(f"- Golden Set: `{GOLDEN_PATH}`")
    lines.append("")
    lines.append("## 逐查询结果")
    lines.append("")
    lines.append("| 查询 | 期望 seed | Top-K 命中 id | 命中 | MRR |")
    lines.append("|------|-----------|---------------|------|-----|")
    for r in results:
        hit = "✓" if r["hit"] else "✗"
        lines.append(
            f"| {r['query']} | {r['expected']} | {r['top_ids']} | {hit} | {r['mrr']:.2f} |"
        )
    lines.append("")
    lines.append("## 解读")
    lines.append("")
    lines.append(
        "- baseline 使用 MiniLM-L6-v2（英文向），中文音乐语境查询的 recall 预期偏低，"
        "这正是升级嵌入模型（BGE-m3 / API 嵌入）并重跑本基准的量化依据。"
    )
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Aud.IO 记忆检索评估")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--embedding", choices=["local", "api"], default="local")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()

    seeds, queries = load_golden()
    results, summary = await run_eval(seeds, queries, args.limit, args.embedding, args.keep_data)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_build_report(results, summary, args.limit), encoding="utf-8")

    print(
        f"\nrecall@{args.limit}: {summary['recall_at_k']:.1%} | MRR: {summary['mrr']:.3f} "
        f"({summary['hits']}/{summary['queries']}) | embedding: {summary['embedding']}"
    )
    print(f"报告已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))