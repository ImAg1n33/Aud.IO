"""数据模型与工具函数 —— EpisodicSnapshot、ChromaDB 元数据常量、时间工具。

RFC-008 Step 1: 从 episodic_memory.py 提取。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# ================================================================
# EpisodicSnapshot
# ================================================================


@dataclass
class EpisodicSnapshot:
    """单次交互的情节快照 —— 与 SQLite episodes 表行一一对应。"""
    id: int
    timestamp: str                # ISO 8601 UTC
    user_input: str               # 用户原始输入
    assistant_reply: str          # Aud.IO 回复文本
    played_song_name: str | None
    played_song_artist: str | None
    mood_tag: str | None          # 自动检测或手动指定的心情标签
    weather_tag: str | None       # 天气标签（预留）
    time_of_day: str              # "morning" / "afternoon" / "evening" / "night"
    genre_tag: str | None         # 歌曲流派标签

    # 语义相似度分数（仅在 vector 查询时填充，SQLite 查询为 None）
    similarity_score: float | None = None

    # RFC-007: 记忆评分与衰减字段（v0.3）
    importance_score: float = 0.5        # 0.0-1.0，由交互深度决定（播放>推荐>闲聊）
    access_count: int = 0               # 累计检索命中次数
    last_accessed: str | None = None    # 最近一次被检索的 ISO 时间戳

    # RFC: 反馈闭环字段（v0.5）—— 由播放事件校准重要性
    song_id: str | None = None          # 歌曲 ID（NetEase），用于反馈事件匹配
    played_to_completion: int = 0       # 1 = 完整播完（正反馈）
    listen_duration: float | None = None  # 最近一次播放收听秒数
    play_count: int = 0                 # 完整播放次数
    skip_count: int = 0                 # 切歌次数
    last_feedback: str | None = None    # started / finished / skipped / failed


# ================================================================
# 时间工具函数
# ================================================================


def _utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _time_of_day() -> str:
    """根据系统本地时间推断当前时段。

    注意：使用系统本地时区而非硬编码 UTC+8（修复了架构报告 #4.1.6）。
    """
    now = datetime.now()
    hour = now.hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    return "night"


# ================================================================
# ChromaDB 元数据列名常量
# ================================================================


class _Meta:
    """ChromaDB metadata 字段名常量。"""
    TIMESTAMP = "timestamp"
    USER_INPUT = "user_input"
    ASSISTANT_REPLY = "assistant_reply"
    SONG_NAME = "played_song_name"
    SONG_ARTIST = "played_song_artist"
    SONG_ID = "song_id"
    MOOD_TAG = "mood_tag"
    WEATHER_TAG = "weather_tag"
    TIME_OF_DAY = "time_of_day"
    GENRE_TAG = "genre_tag"
    SESSION_ID = "session_id"
    # 用于 ChromaDB metadata 过滤的最大字符长度（避免元数据过大）
    MAX_TEXT_LEN = 500


# ================================================================
# SQLite 行 → EpisodicSnapshot 映射
# ================================================================


def _row_to_snapshot(row: tuple[Any, ...]) -> EpisodicSnapshot:
    """将 SQLite 行元组映射为 EpisodicSnapshot。"""
    # Decay fields are at indices 11-13 (added by migration v2).
    # Handle pre-migration rows gracefully (fewer columns).
    importance = float(row[11]) if len(row) > 11 and row[11] is not None else 0.5
    access_count = int(row[12]) if len(row) > 12 and row[12] is not None else 0
    last_accessed = str(row[13]) if len(row) > 13 and row[13] is not None else None

    # Feedback fields at indices 14-19 (added by migration v3).
    song_id = str(row[14]) if len(row) > 14 and row[14] is not None else None
    played_to_completion = int(row[15]) if len(row) > 15 and row[15] is not None else 0
    listen_duration = float(row[16]) if len(row) > 16 and row[16] is not None else None
    play_count = int(row[17]) if len(row) > 17 and row[17] is not None else 0
    skip_count = int(row[18]) if len(row) > 18 and row[18] is not None else 0
    last_feedback = str(row[19]) if len(row) > 19 and row[19] is not None else None

    return EpisodicSnapshot(
        id=row[0],
        timestamp=row[1],
        user_input=row[2],
        assistant_reply=row[3],
        played_song_name=row[4],
        played_song_artist=row[5],
        mood_tag=row[6],
        weather_tag=row[7],
        time_of_day=row[8],
        genre_tag=row[9],
        similarity_score=None,
        importance_score=importance,
        access_count=access_count,
        last_accessed=last_accessed,
        song_id=song_id,
        played_to_completion=played_to_completion,
        listen_duration=listen_duration,
        play_count=play_count,
        skip_count=skip_count,
        last_feedback=last_feedback,
    )
