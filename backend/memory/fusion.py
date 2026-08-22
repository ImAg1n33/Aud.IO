"""混合检索融合 —— Reciprocal Rank Fusion（RRF）。

语义检索（ChromaDB 向量）与关键词检索（SQLite LIKE）各产生一份排名，
RRF 按排名位置融合：score(id) = Σ 1/(k + rank_i)，同一 id 在多个列表中
出现即得分累加。对个人记忆库（<10K 条）足够鲁棒，无需调权。

参考 GWKB 混合检索方法论（PG FTS + Qdrant + RRF），此处做轻量落地。
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.memory.models import EpisodicSnapshot


def rrf_fuse(
    semantic: Iterable[EpisodicSnapshot],
    keyword: Iterable[EpisodicSnapshot],
    k: int = 60,
) -> list[EpisodicSnapshot]:
    """融合语义与关键词两路候选，按 RRF 分数降序返回（按 id 去重）。

    Args:
        semantic: 语义检索结果（按相似度降序，带 similarity_score）
        keyword: 关键词 LIKE 结果（按时间倒序）
        k: RRF 平滑常数（默认 60，与业界惯例一致）

    Returns:
        去重后的融合列表（保留语义检索的 similarity_score 供衰减重排）。
    """
    scores: dict[int, float] = {}
    by_id: dict[int, EpisodicSnapshot] = {}

    for rank, snap in enumerate(semantic, start=1):
        scores[snap.id] = scores.get(snap.id, 0.0) + 1.0 / (k + rank)
        by_id[snap.id] = snap

    for rank, snap in enumerate(keyword, start=1):
        scores[snap.id] = scores.get(snap.id, 0.0) + 1.0 / (k + rank)
        if snap.id not in by_id:
            by_id[snap.id] = snap

    ranked = sorted(by_id.values(), key=lambda s: scores[s.id], reverse=True)
    return ranked