"""记忆衰减评分引擎 —— Ebbinghaus 遗忘曲线 + 访问频率加权。

RFC-008 Step 1: 从 episodic_memory.py 提取 compute_decayed_score 纯函数。
"""

import math
from datetime import datetime


def compute_decayed_score(
    semantic_sim: float,
    importance: float,
    access_count: int,
    last_accessed: str | None,
    created_at: str,
    now_iso: str,
) -> float:
    """Compute a weighted retrieval score combining semantic similarity
    with memory decay factors.

    Weights (tuned for a personal music DJ — recent & important > old):
      semantic_sim * 0.50  — vector similarity (ChromaDB cosine)
      importance    * 0.20  — how valuable this memory is
      freshness     * 0.20  — Ebbinghaus decay since last access
      access_bonus  * 0.10  — frequently accessed memories stay "hot"

    Returns a score in [0.0, 1.0].
    """
    # Importance: clamp to valid range
    imp = max(0.0, min(1.0, importance))

    # Freshness: Ebbinghaus-inspired decay since last access (or creation)
    ref_ts = last_accessed or created_at
    try:
        ref_dt = datetime.fromisoformat(ref_ts.replace("Z", "+00:00"))
        now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        hours_elapsed = (now_dt - ref_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        hours_elapsed = 24.0  # Fallback: treat as 1 day old

    # Half-life of ~7 days (168 hours) for un-reinforced memories
    freshness = math.exp(-hours_elapsed / 168.0)

    # Access bonus: log-scale, saturates around 10 accesses
    access_bonus = math.log(access_count + 1) / math.log(11)

    return (
        semantic_sim * 0.50
        + imp * 0.20
        + freshness * 0.20
        + access_bonus * 0.10
    )
