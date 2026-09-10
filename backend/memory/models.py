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
    dislike_count: int = 0              # 显式不喜欢次数（v0.6 拒绝学习）


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


def _row_to_snapshot(row: Any) -> EpisodicSnapshot:
    """将 SQLite 行映射为 EpisodicSnapshot。

    v0.7 修复：优先按**列名**读取（sqlite3.Row）——位置映射曾因生产库历史
    迁移顺序异常（last_accessed 缺列晚补）而错位，把 played_to_completion(0)
    读成 song_id='0'。名称读取与 schema 顺序无关；tuple 传入时退化为位置读取。
    """
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    def _get(name: str, index: int, default: Any = None) -> Any:
        if keys:
            return row[name] if name in keys else default
        return row[index] if len(row) > index else default

    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def _as_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _as_str(value: Any) -> str | None:
        return str(value) if value is not None else None

    importance = _as_float(_get("importance_score", 11))
    song_id = _as_str(_get("song_id", 14))

    return EpisodicSnapshot(
        id=_as_int(_get("id", 0)),
        timestamp=str(_get("timestamp", 1) or ""),
        user_input=str(_get("user_input", 2) or ""),
        assistant_reply=str(_get("assistant_reply", 3) or ""),
        played_song_name=_as_str(_get("played_song_name", 4)),
        played_song_artist=_as_str(_get("played_song_artist", 5)),
        mood_tag=_as_str(_get("mood_tag", 6)),
        weather_tag=_as_str(_get("weather_tag", 7)),
        time_of_day=str(_get("time_of_day", 8) or "unknown"),
        genre_tag=_as_str(_get("genre_tag", 9)),
        similarity_score=None,
        importance_score=importance if importance is not None else 0.5,
        access_count=_as_int(_get("access_count", 12)),
        last_accessed=_as_str(_get("last_accessed", 13)),
        song_id=song_id,
        played_to_completion=_as_int(_get("played_to_completion", 15)),
        listen_duration=_as_float(_get("listen_duration", 16)),
        play_count=_as_int(_get("play_count", 17)),
        skip_count=_as_int(_get("skip_count", 18)),
        last_feedback=_as_str(_get("last_feedback", 19)),
        dislike_count=_as_int(_get("dislike_count", 20)),
    )
