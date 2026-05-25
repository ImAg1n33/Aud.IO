"""情节记忆 —— ChromaDB 向量存储 + SQLite 向后兼容的双写架构。

架构演进（Phase 1 双写过渡）:
- 写入路径: store_snapshot() 同时写入 ChromaDB（主存储，支持语义检索）和 SQLite（向后兼容）
- 读取路径: 优先走 ChromaDB（向量相似度 + 元数据过滤），SQLite 作为 fallback
- 统计查询: get_preference_stats() 暂保留 SQLite（SQL GROUP BY 更适合聚合统计）
- Phase 2 待验证稳定后，可移除 SQLite 写入和 fallback 逻辑

ChromaDB 存储模型:
- Collection: "episodes"
- Document: user_input（被向量化的文本 —— 用户的原始输入）
- Metadata: 时间戳、assistant_reply 摘要、歌曲信息、mood/weather/genre 标签、time_of_day
- ID: 与 SQLite episodes 表的主键 ID 一致，保证双写可对账
"""

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.memory.embedding import (
    EmbeddingProvider,
    create_embedding_provider,
)

logger = logging.getLogger(__name__)

# ================================================================
# 数据模型（保持向后兼容，字段不变）
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


# ================================================================
# 工具函数（从旧实现保留，签名不变）
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
# ChromaDB 元数据列名常量（方便统一修改）
# ================================================================

class _Meta:
    """ChromaDB metadata 字段名常量。"""
    TIMESTAMP = "timestamp"
    USER_INPUT = "user_input"
    ASSISTANT_REPLY = "assistant_reply"
    SONG_NAME = "played_song_name"
    SONG_ARTIST = "played_song_artist"
    MOOD_TAG = "mood_tag"
    WEATHER_TAG = "weather_tag"
    TIME_OF_DAY = "time_of_day"
    GENRE_TAG = "genre_tag"
    SESSION_ID = "session_id"
    # 用于 ChromaDB metadata 过滤的最大字符长度（避免元数据过大）
    MAX_TEXT_LEN = 500


# ================================================================
# Mood 自动检测 —— 基于关键词的轻量分类器（无需 LLM，零延迟）
# ================================================================

class MoodDetector:
    """基于中英文关键词的心情检测器。

    设计理由:
    - 零 LLM 成本、零网络延迟 —— 关键词匹配在 < 1ms 内完成
    - 覆盖常见音乐场景心情词（轻松、专注、悲伤、兴奋、平静、浪漫、困倦等）
    - 返回的 mood 标签与 user_profile.json 的 mood_bias 键名对齐，
      确保后续偏好推荐能命中

    注意:
    - 如果用户输入中没有匹配到任何关键词，返回 None（不做猜测）
    - 这不是最终方案 —— Phase 2 可升级为 embedding 相似度分类
    """

    # 中文关键词 → 英文 mood 标签（与 user_profile.json mood_bias 键对齐）
    _CN_TO_MOOD: dict[str, str] = {
        # 轻松 / chill
        "轻松": "calm", "放松": "calm", "舒缓": "calm", "轻快": "calm",
        "休闲": "calm", "chill": "calm", "relax": "calm",
        # 专注 / 工作
        "专注": "focused", "工作": "focused", "学习": "focused",
        "看书": "focused", "阅读": "focused", "编程": "focused",
        "coding": "focused", "study": "focused", "focus": "focused",
        # 开心 / 兴奋
        "开心": "happy", "高兴": "happy", "快乐": "happy", "嗨": "happy",
        "兴奋": "happy", "激动": "happy", "蹦迪": "happy", "派对": "happy",
        "party": "happy", "happy": "happy", "energetic": "happy",
        # 悲伤 / 低落
        "难过": "sad", "悲伤": "sad", "伤心": "sad", "低落": "sad",
        "emo": "sad", "sad": "sad", "忧郁": "sad", "抑郁": "sad",
        # 安静 / 平和
        "安静": "calm", "宁静": "calm", "平和": "calm",
        "peaceful": "calm", "quiet": "calm",
        # 浪漫
        "浪漫": "romantic", "浪漫主义": "romantic", "约会": "romantic",
        "romantic": "romantic", "date": "romantic",
        # 雨天
        "下雨": "rainy", "雨天": "rainy", "雨声": "rainy",
        "rain": "rainy", "rainy": "rainy",
        # 困倦 / 深夜
        "困": "sleepy", "困了": "sleepy", "睡觉": "sleepy", "入睡": "sleepy",
        "催眠": "sleepy", "sleep": "sleepy", "sleepy": "sleepy",
        # 运动
        "运动": "energetic", "跑步": "energetic", "健身": "energetic",
        "锻炼": "energetic", "workout": "energetic", "gym": "energetic",
        # 开车 / 旅行
        "开车": "driving", "驾驶": "driving", "旅途": "driving",
        "旅行": "driving", "公路": "driving", "drive": "driving",
        # 怀旧
        "怀旧": "nostalgic", "回忆": "nostalgic", "老歌": "nostalgic",
        "nostalgia": "nostalgic", "nostalgic": "nostalgic",
    }

    # 优先级更高的关键词（长度更长、更具体的关键词优先匹配）
    _PRIORITY_KEYWORDS: list[str] = [
        # 长关键词排在前面，确保优先匹配
        "浪漫主义", "coding", "study", "focus", "happy", "energetic",
        "workout", "sleep", "sleepy", "rainy", "rain", "sad",
        "party", "chill", "relax", "quiet", "peaceful",
        "romantic", "nostalgic", "nostalgia", "drive",
    ]

    @classmethod
    def detect(cls, user_input: str) -> str | None:
        """从用户输入中检测心情标签。

        Args:
            user_input: 用户原始输入文本

        Returns:
            检测到的英文 mood 标签，无匹配时返回 None
        """
        if not user_input or not user_input.strip():
            return None

        text = user_input.strip()
        text_lower = text.lower()

        matched_mood: str | None = None
        matched_len: int = 0

        # 遍历中英文关键词表，取最长匹配（避免 "困" 误匹配 "困难"）
        for keyword, mood in cls._CN_TO_MOOD.items():
            kw_lower = keyword.lower()
            if kw_lower in text_lower or keyword in text:
                kw_len = len(keyword)
                # 更长关键词优先；同长度时优先级列表中靠前的优先
                if kw_len > matched_len or (
                    kw_len == matched_len
                    and cls._priority_score(keyword) > cls._priority_score(matched_mood or "")
                ):
                    matched_mood = mood
                    matched_len = kw_len

        return matched_mood

    @classmethod
    def _priority_score(cls, keyword: str) -> int:
        """计算关键词优先级分数（越大越优先）。"""
        try:
            # 低索引 = 高优先级
            idx = cls._PRIORITY_KEYWORDS.index(keyword.lower())
            return len(cls._PRIORITY_KEYWORDS) - idx
        except ValueError:
            return 0


# ================================================================
# EpisodicMemory —— 双写架构主类
# ================================================================

class EpisodicMemory:
    """情节记忆存储 —— ChromaDB 向量检索 + SQLite 向后兼容。

    公开 API（与旧版完全兼容）:
        store_snapshot()      —— 持久化一次对话交互
        query_recent()        —— 按时间倒序获取最近记录
        query_by_tags()       —— 按 mood/time/genre 标签精确过滤
        query_by_keyword()    —— 文本关键词 LIKE 搜索
        get_preference_stats() —— SQL 聚合统计（流派、艺人、心情相关性）
        format_stats_for_prompt() —— 将统计数据格式化为 LLM 可读文本

    新增 API（向量检索能力）:
        query_by_semantic()   —— 基于语义相似度的向量检索，替代旧 mood 关键词映射

    使用示例:
        # 自动选择 Embedding provider（默认本地 ONNX）
        memory = EpisodicMemory()

        # 指定远端 API embedding
        from backend.memory.embedding import APIEmbedding
        memory = EpisodicMemory(
            embedding_provider=APIEmbedding(model="text-embedding-3-small"),
        )

        # 存储快照（自动检测 mood）
        await memory.store_snapshot("放点轻松的爵士", "好的，来点 Miles Davis...")

        # 语义检索 —— "适合下雨天听的" 自动匹配 rainy/jazz 相关记录
        results = await memory.query_by_semantic("下雨天适合听什么", limit=5)
    """

    def __init__(
        self,
        db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        """初始化情节记忆存储。

        Args:
            db_path: SQLite 数据库文件路径（None = 默认 backend/memory/episodes.db）
            embedding_provider: 向量嵌入提供者（None = 根据环境变量自动选择）
        """
        # --- 路径初始化（兼容 str 和 Path） ---
        if db_path is None:
            backend_root = Path(__file__).resolve().parents[1]
            db_path = backend_root / "memory" / "episodes.db"
        self.db_path = Path(db_path)  # 确保是 Path 对象，兼容调用方传 str
        self._chroma_path = str(self.db_path.parent / "chroma_episodes")

        # --- Embedding provider ---
        self._embed = embedding_provider or create_embedding_provider()

        # --- SQLite 初始化（向后兼容） ---
        self._init_sqlite()

        # --- ChromaDB 初始化（主存储） ---
        self._init_chroma()

    # ================================================================
    # 初始化
    # ================================================================

    def _init_sqlite(self) -> None:
        """创建 SQLite 表及索引（与旧版 schema 完全一致）。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    assistant_reply TEXT NOT NULL DEFAULT '',
                    played_song_name TEXT,
                    played_song_artist TEXT,
                    mood_tag TEXT,
                    weather_tag TEXT,
                    time_of_day TEXT NOT NULL DEFAULT 'unknown',
                    genre_tag TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_timestamp
                ON episodes(timestamp DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_episodes_tags
                ON episodes(mood_tag, weather_tag, time_of_day)
            """)
            conn.commit()
        # v0.3 migration: add session_id column for multi-user isolation
        self._migrate_sqlite_session_id()

    def _migrate_sqlite_session_id(self) -> None:
        """Add session_id column if it doesn't exist (v0.3 multi-user isolation)."""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "ALTER TABLE episodes ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'"
                )
                conn.commit()
            logger.info("SQLite migration: added session_id column to episodes")
        except sqlite3.OperationalError:
            pass  # Column already exists

    def _init_chroma(self) -> None:
        """初始化 ChromaDB PersistentClient 及 episodes collection。

        ChromaDB 的 PersistentClient 使用 SQLite3 作为自己的元数据存储，
        数据文件位于 self._chroma_path 目录下，与我们的 episodes.db 独立。
        """
        try:
            import chromadb
        except ImportError:
            logger.error(
                "ChromaDB 未安装，无法启用向量检索。"
                "请执行: pip install chromadb"
            )
            raise

        # PersistentClient —— 数据持久化到磁盘，进程内运行，无需额外服务
        self._chroma_client = chromadb.PersistentClient(path=self._chroma_path)

        # 获取或创建 collection
        # 注意：不传 embedding_function 参数 —— 我们在外部手动计算 embedding，
        # 这样 ChromeLocalEmbedding 和 APIEmbedding 走统一的代码路径
        self._collection = self._chroma_client.get_or_create_collection(
            name="episodes",
            metadata={"hnsw:space": "cosine"},  # 余弦相似度，适合语义检索
        )

        logger.info(
            "ChromaDB 初始化完成: collection='episodes', "
            "path=%s, count=%d",
            self._chroma_path,
            self._collection.count(),
        )

        # v0.3 migration: backfill session_id for existing entries
        self._migrate_chroma_session_id()

    def _migrate_chroma_session_id(self) -> None:
        """Backfill existing ChromaDB entries without session_id."""
        try:
            results = self._collection.get(include=["metadatas"])
            if not results["ids"]:
                return
            ids_to_update: list[str] = []
            metadatas_to_update: list[dict[str, str]] = []
            for i, cid in enumerate(results["ids"]):
                meta = results["metadatas"][i] if i < len(results["metadatas"]) else {}
                if _Meta.SESSION_ID not in meta or not meta[_Meta.SESSION_ID]:
                    meta[_Meta.SESSION_ID] = "default"
                    ids_to_update.append(cid)
                    metadatas_to_update.append(meta)
            if ids_to_update:
                self._collection.update(ids=ids_to_update, metadatas=metadatas_to_update)
                logger.info(
                    "ChromaDB migration: backfilled session_id for %d entries",
                    len(ids_to_update),
                )
        except Exception as exc:
            logger.warning("ChromaDB session_id migration skipped: %s", exc)

    # ================================================================
    # 写入路径 —— Phase 1 双写（ChromaDB + SQLite）
    # ================================================================

    async def store_snapshot(
        self,
        user_input: str,
        assistant_reply: str = "",
        played_song: dict[str, Any] | None = None,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        genre_tag: str | None = None,
        session_id: str = "default",
    ) -> int:
        """持久化一次对话交互快照（双写 ChromaDB + SQLite）。

        Args:
            user_input: 用户原始输入文本
            assistant_reply: Aud.IO 回复文本
            played_song: 播放的歌曲信息 {"name": ..., "artist": ..., ...}
            mood_tag: 心情标签（None = 自动检测）
            weather_tag: 天气标签（预留）
            genre_tag: 歌曲流派标签
            session_id: 会话标识符（用于多用户隔离）

        Returns:
            新插入记录的 ID（与 SQLite episodes.id 一致）

        写入流程:
            1. 自动检测 mood（如果调用方未提供）
            2. 先写 SQLite（获取自增 ID）
            3. 用相同 ID 写 ChromaDB（含向量嵌入）
            4. 如果 ChromaDB 写入失败，记录错误但不阻塞（SQLite 已写成功）
        """
        # --- 提取歌曲信息 ---
        song_name = None
        song_artist = None
        if isinstance(played_song, dict):
            song_name = str(played_song.get("name", "")) or None
            song_artist = str(played_song.get("artist", "")) or None

        # --- 自动检测心情标签 ---
        if mood_tag is None:
            mood_tag = MoodDetector.detect(user_input)

        time_of_day = _time_of_day()
        timestamp = _utc_now_iso()

        # --- 1) SQLite 写入（获取自增 ID） ---
        row_id = await asyncio.to_thread(
            self._insert_sqlite,
            timestamp,
            user_input,
            assistant_reply,
            song_name,
            song_artist,
            mood_tag,
            weather_tag,
            time_of_day,
            genre_tag,
            session_id,
        )

        # --- 2) ChromaDB 写入（携带向量嵌入） ---
        await self._upsert_chroma(
            row_id=row_id,
            user_input=user_input,
            timestamp=timestamp,
            assistant_reply=assistant_reply,
            song_name=song_name,
            song_artist=song_artist,
            mood_tag=mood_tag,
            weather_tag=weather_tag,
            time_of_day=time_of_day,
            genre_tag=genre_tag,
            session_id=session_id,
        )

        if mood_tag:
            logger.debug(
                "情节记忆已存储: id=%d, mood=%s, time=%s, input=%.60s...",
                row_id, mood_tag, time_of_day, user_input,
            )
        else:
            logger.debug(
                "情节记忆已存储: id=%d, mood=(未检测到), time=%s, input=%.60s...",
                row_id, time_of_day, user_input,
            )

        return row_id

    def _insert_sqlite(
        self,
        timestamp: str,
        user_input: str,
        assistant_reply: str,
        song_name: str | None,
        song_artist: str | None,
        mood_tag: str | None,
        weather_tag: str | None,
        time_of_day: str,
        genre_tag: str | None,
        session_id: str = "default",
    ) -> int:
        """同步 SQLite 插入，返回自增 ID。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """INSERT INTO episodes
                   (timestamp, user_input, assistant_reply, played_song_name, played_song_artist,
                    mood_tag, weather_tag, time_of_day, genre_tag, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    user_input,
                    assistant_reply,
                    song_name,
                    song_artist,
                    mood_tag,
                    weather_tag,
                    time_of_day,
                    genre_tag,
                    session_id,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    async def _upsert_chroma(
        self,
        row_id: int,
        user_input: str,
        timestamp: str,
        assistant_reply: str,
        song_name: str | None,
        song_artist: str | None,
        mood_tag: str | None,
        weather_tag: str | None,
        time_of_day: str,
        genre_tag: str | None,
        session_id: str = "default",
    ) -> None:
        """向 ChromaDB collection 写入或更新一条记录（携带向量）。"""
        try:
            # 计算 user_input 的向量嵌入
            embeddings = await self._embed.embed([user_input])
        except Exception as exc:
            logger.error("向量嵌入计算失败，跳过 ChromaDB 写入 (id=%d): %s", row_id, exc)
            return

        # 构建 metadata —— 注意限制文本长度避免 ChromaDB 元数据过大
        metadata: dict[str, str] = {
            _Meta.TIMESTAMP: timestamp,
            _Meta.USER_INPUT: user_input[:_Meta.MAX_TEXT_LEN],
            _Meta.ASSISTANT_REPLY: assistant_reply[:_Meta.MAX_TEXT_LEN],
            _Meta.SONG_NAME: song_name or "",
            _Meta.SONG_ARTIST: song_artist or "",
            _Meta.MOOD_TAG: mood_tag or "",
            _Meta.WEATHER_TAG: weather_tag or "",
            _Meta.TIME_OF_DAY: time_of_day,
            _Meta.GENRE_TAG: genre_tag or "",
            _Meta.SESSION_ID: session_id,
        }

        try:
            # upsert: 如果 ID 已存在则更新（幂等），否则插入
            self._collection.upsert(
                ids=[str(row_id)],
                embeddings=embeddings,
                documents=[user_input],
                metadatas=[metadata],
            )
        except Exception as exc:
            logger.error("ChromaDB 写入失败 (id=%d): %s", row_id, exc)
            # 不重新抛出 —— SQLite 已经写成功，ChromaDB 失败不影响主流程

    # ================================================================
    # 读取路径 —— 语义检索（新增能力）
    # ================================================================

    async def query_by_semantic(
        self,
        query_text: str,
        *,
        mood_tag: str | None = None,
        time_of_day: str | None = None,
        genre_tag: str | None = None,
        session_id: str | None = None,
        limit: int = 5,
    ) -> list[EpisodicSnapshot]:
        """基于语义相似度的向量检索 —— 替代旧版关键词 mood 映射。

        与旧版 query_by_tags() 的关键区别:
        - 旧版: WHERE mood_tag = 'calm'  —— 只能精确匹配标签
        - 新版: 对 query_text 做向量嵌入，在 ChromaDB 中按余弦相似度排序，
                同时支持 metadata 精确过滤

        使用示例:
            # "下雨天适合听的" 语义匹配到 rainy/jazz 相关的历史交互
            results = await memory.query_by_semantic("下雨天适合听什么")

            # 语义 + 时间过滤 —— "晚上工作时听的专注音乐"
            results = await memory.query_by_semantic(
                "工作时要专注",
                time_of_day="night",
                limit=3,
            )

        Args:
            query_text: 查询文本（用户的自然语言输入）
            mood_tag: 可选的心情标签精确过滤
            time_of_day: 可选的时段精确过滤
            genre_tag: 可选的流派精确过滤
            session_id: 可选的会话标识符（None = 不过滤）
            limit: 返回记录数上限

        Returns:
            按语义相似度降序排列的 EpisodicSnapshot 列表，
            每个 snapshot 的 similarity_score 字段包含余弦距离（1.0 = 完全匹配）
        """
        # --- 构建 ChromaDB where 过滤条件 ---
        where_filter = self._build_chroma_where(
            mood_tag=mood_tag,
            time_of_day=time_of_day,
            genre_tag=genre_tag,
            session_id=session_id,
        )

        # --- 计算查询文本的向量嵌入 ---
        try:
            query_embeddings = await self._embed.embed([query_text])
        except Exception as exc:
            logger.error("查询向量嵌入失败，fallback 到 SQLite 关键词检索: %s", exc)
            return await self._fallback_keyword_query(query_text, limit, session_id)

        # --- ChromaDB 向量检索 ---
        try:
            results = self._collection.query(
                query_embeddings=query_embeddings,
                n_results=limit,
                where=where_filter if where_filter else None,
                include=["metadatas", "documents", "distances"],
            )
        except Exception as exc:
            logger.error("ChromaDB 查询失败，fallback 到 SQLite: %s", exc)
            return await self._fallback_keyword_query(query_text, limit, session_id)

        # --- 将 ChromaDB 结果映射为 EpisodicSnapshot 列表 ---
        return self._chroma_results_to_snapshots(results)

    # ================================================================
    # 读取路径 —— 标签精确检索（ChromaDB metadata 过滤，替代旧 SQL WHERE）
    # ================================================================

    async def query_by_tags(
        self,
        *,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        time_of_day: str | None = None,
        genre_tag: str | None = None,
        session_id: str | None = None,
        limit: int = 5,
    ) -> list[EpisodicSnapshot]:
        """按标签精确过滤查询 —— Phase 1 优先走 ChromaDB metadata 过滤。

        与旧版完全相同的签名和行为：
        - 如果没有任何过滤条件 → 返回最近记录
        - 支持 mood_tag / weather_tag / time_of_day / genre_tag / session_id 的 AND 组合
        """
        if not any([mood_tag, weather_tag, time_of_day, genre_tag, session_id]):
            return await self.query_recent(limit=limit, session_id=session_id)

        # --- 构建 ChromaDB where 过滤 ---
        where_filter = self._build_chroma_where(
            mood_tag=mood_tag,
            weather_tag=weather_tag,
            time_of_day=time_of_day,
            genre_tag=genre_tag,
            session_id=session_id,
        )

        try:
            # ChromaDB get 支持 where 过滤 + 排序
            results = self._collection.get(
                where=where_filter,
                limit=limit,
                include=["metadatas", "documents"],
            )
        except Exception as exc:
            logger.error("ChromaDB 标签查询失败，fallback 到 SQLite: %s", exc)
            return await self._fallback_tags_query(
                mood_tag, weather_tag, time_of_day, genre_tag, limit, session_id,
            )

        if not results["ids"]:
            return []

        # 按 timestamp 降序排列（ChromaDB get 不保证顺序）
        snapshots = self._metadata_list_to_snapshots(
            results["ids"],
            results["metadatas"],
            results.get("documents"),
        )
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots[:limit]

    # ================================================================
    # 读取路径 —— 保留的旧版查询方法
    # ================================================================

    async def query_recent(self, limit: int = 10, session_id: str | None = None) -> list[EpisodicSnapshot]:
        """获取最近 N 条记录（SQLite 查询，简单可靠）。"""
        def _query() -> list[EpisodicSnapshot]:
            with sqlite3.connect(str(self.db_path)) as conn:
                if session_id:
                    rows = conn.execute(
                        "SELECT * FROM episodes WHERE session_id = ? "
                        "ORDER BY timestamp DESC LIMIT ?",
                        (session_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [_row_to_snapshot(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def query_by_keyword(self, keyword: str, limit: int = 5, session_id: str | None = None) -> list[EpisodicSnapshot]:
        """文本关键词 LIKE 搜索 —— 在 user_input 和 assistant_reply 中匹配。

        注意：这是传统的 SQL LIKE 搜索，不是语义搜索。
        语义搜索请使用 query_by_semantic()。
        """
        pattern = f"%{keyword}%"

        def _query() -> list[EpisodicSnapshot]:
            with sqlite3.connect(str(self.db_path)) as conn:
                if session_id:
                    rows = conn.execute(
                        """SELECT * FROM episodes
                           WHERE (user_input LIKE ? OR assistant_reply LIKE ?)
                           AND session_id = ?
                           ORDER BY timestamp DESC LIMIT ?""",
                        (pattern, pattern, session_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT * FROM episodes
                           WHERE user_input LIKE ? OR assistant_reply LIKE ?
                           ORDER BY timestamp DESC LIMIT ?""",
                        (pattern, pattern, limit),
                    ).fetchall()
                return [_row_to_snapshot(row) for row in rows]

        return await asyncio.to_thread(_query)

    # ================================================================
    # 统计查询 —— 保留 SQLite（SQL 聚合更高效）
    # ================================================================

    async def get_preference_stats(self, session_id: str | None = None) -> dict[str, Any]:
        """基于情节记忆的 SQL 聚合统计 —— 生成数据驱动的用户偏好报表。

        统计维度:
        - top_genres: 最常播放的流派排名
        - top_artists: 最常播放的艺人排名
        - mood_genre_correlations: 心情-流派相关性矩阵
        - time_patterns: 时段-流派播放模式
        - total_episodes: 总记录数

        注意：
        - 现在 mood_tag 已被自动检测并写入，mood_genre_correlations 不再总是空
        - 此方法保留 SQLite 实现，因为 SQL GROUP BY 比 ChromaDB 的元数据聚合更高效
        """
        # Build WHERE fragment: "WHERE session_id = ?" or empty
        where_prefix = "WHERE session_id = ? AND " if session_id else "WHERE "
        params: tuple = (session_id,) if session_id else ()

        def _compute() -> dict[str, Any]:
            with sqlite3.connect(str(self.db_path)) as conn:
                # 流派排行
                genre_rows = conn.execute(
                    f"""SELECT genre_tag, COUNT(*) as cnt FROM episodes
                       {where_prefix}genre_tag IS NOT NULL AND genre_tag != ''
                       GROUP BY genre_tag ORDER BY cnt DESC LIMIT 8""",
                    params,
                ).fetchall()
                top_genres = [{"genre": row[0], "count": row[1]} for row in genre_rows]

                # 艺人排行
                artist_rows = conn.execute(
                    f"""SELECT played_song_artist, COUNT(*) as cnt FROM episodes
                       {where_prefix}played_song_artist IS NOT NULL AND played_song_artist != ''
                       GROUP BY played_song_artist ORDER BY cnt DESC LIMIT 8""",
                    params,
                ).fetchall()
                top_artists = [{"artist": row[0], "count": row[1]} for row in artist_rows]

                # 心情-流派相关性（现在 mood_tag 会被自动填充，不再总是空）
                mood_genre_rows = conn.execute(
                    f"""SELECT mood_tag, genre_tag, COUNT(*) as cnt FROM episodes
                       {where_prefix}mood_tag IS NOT NULL AND mood_tag != ''
                         AND genre_tag IS NOT NULL AND genre_tag != ''
                       GROUP BY mood_tag, genre_tag ORDER BY cnt DESC LIMIT 12""",
                    params,
                ).fetchall()
                mood_genre = [
                    {"mood": row[0], "genre": row[1], "count": row[2]}
                    for row in mood_genre_rows
                ]

                # 时段-流派模式
                time_rows = conn.execute(
                    f"""SELECT time_of_day, genre_tag, COUNT(*) as cnt FROM episodes
                       {where_prefix}genre_tag IS NOT NULL AND genre_tag != ''
                       GROUP BY time_of_day, genre_tag ORDER BY cnt DESC LIMIT 12""",
                    params,
                ).fetchall()
                time_patterns = [
                    {"time": row[0], "genre": row[1], "count": row[2]}
                    for row in time_rows
                ]

                # 总量
                total_sql = (
                    "SELECT COUNT(*) FROM episodes WHERE session_id = ?"
                    if session_id
                    else "SELECT COUNT(*) FROM episodes"
                )
                total_row = conn.execute(total_sql, params).fetchone()
                total = total_row[0] if total_row else 0

            return {
                "total_episodes": total,
                "top_genres": top_genres,
                "top_artists": top_artists,
                "mood_genre_correlations": mood_genre,
                "time_patterns": time_patterns,
            }

        return await asyncio.to_thread(_compute)

    def format_stats_for_prompt(self, stats: dict[str, Any]) -> str | None:
        """将统计数据格式化为 LLM 可读的 prompt 文本块。

        Args:
            stats: get_preference_stats() 的返回结果

        Returns:
            格式化的文本块，无数据时返回 None
        """
        if not stats or stats.get("total_episodes", 0) == 0:
            return None

        lines = ["[Data-driven insights from your listening history]"]

        top_genres = stats.get("top_genres", [])
        if top_genres:
            genre_str = ", ".join(f"{g['genre']}({g['count']}x)" for g in top_genres[:5])
            lines.append(f"Most played genres: {genre_str}")

        top_artists = stats.get("top_artists", [])
        if top_artists:
            artist_str = ", ".join(f"{a['artist']}({a['count']}x)" for a in top_artists[:5])
            lines.append(f"Most played artists: {artist_str}")

        mood_corr = stats.get("mood_genre_correlations", [])
        if mood_corr:
            lines.append("Mood-genre patterns:")
            for mc in mood_corr[:6]:
                lines.append(f"  {mc['mood']} → {mc['genre']} ({mc['count']}x)")

        time_pat = stats.get("time_patterns", [])
        if time_pat:
            lines.append("Time-of-day patterns:")
            for tp in time_pat[:6]:
                lines.append(f"  {tp['time']} → {tp['genre']} ({tp['count']}x)")

        return "\n".join(lines)

    # ================================================================
    # 工具函数（同步 ChromaDB 查询 → snapshot 映射）
    # ================================================================

    def _build_chroma_where(
        self,
        *,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        time_of_day: str | None = None,
        genre_tag: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """将标签过滤参数转换为 ChromaDB metadata where 过滤条件。

        ChromaDB where 语法:
        - 单条件: {"field": "value"}
        - AND 组合: {"$and": [{"field1": "v1"}, {"field2": "v2"}]}
        - 空字符串视为"未设置"，需要额外排除
        """
        conditions: list[dict[str, Any]] = []

        if mood_tag and mood_tag.strip():
            conditions.append({_Meta.MOOD_TAG: mood_tag.strip()})
        if weather_tag and weather_tag.strip():
            conditions.append({_Meta.WEATHER_TAG: weather_tag.strip()})
        if time_of_day and time_of_day.strip():
            conditions.append({_Meta.TIME_OF_DAY: time_of_day.strip()})
        if genre_tag and genre_tag.strip():
            conditions.append({_Meta.GENRE_TAG: genre_tag.strip()})
        if session_id and session_id.strip():
            conditions.append({_Meta.SESSION_ID: session_id.strip()})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _chroma_results_to_snapshots(
        self, results: dict[str, Any]
    ) -> list[EpisodicSnapshot]:
        """将 ChromaDB query() 返回结果映射为 EpisodicSnapshot 列表。

        ChromaDB query 返回格式:
        {
            "ids": [["id1", "id2", ...]],
            "metadatas": [[{...}, {...}, ...]],
            "documents": [["doc1", "doc2", ...]],
            "distances": [[0.12, 0.34, ...]],
        }
        每个字段都是嵌套列表（外层对应多个 query embedding，内层对应结果列表）。
        """
        ids_list = results.get("ids", [[]])
        metas_list = results.get("metadatas", [[]])
        docs_list = results.get("documents", [[]])
        dists_list = results.get("distances", [[]])

        # 取第一个 query embedding 的结果列表
        ids = ids_list[0] if ids_list else []
        metas = metas_list[0] if metas_list else []
        docs = docs_list[0] if docs_list else []
        dists = dists_list[0] if dists_list else []

        snapshots: list[EpisodicSnapshot] = []
        for i, chroma_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            doc = docs[i] if i < len(docs) else ""
            distance = dists[i] if i < len(dists) else None

            # 将余弦距离转换为 0-1 相似度分数（ChromaDB cosine distance → similarity）
            # cosine distance ∈ [0, 2]，cosine similarity ∈ [-1, 1]
            # similarity ≈ 1 - distance/2（映射到 [0, 1]）
            similarity = None
            if distance is not None:
                similarity = round(max(0.0, 1.0 - distance / 2.0), 4)

            try:
                row_id = int(chroma_id)
            except (ValueError, TypeError):
                row_id = 0

            snapshots.append(EpisodicSnapshot(
                id=row_id,
                timestamp=meta.get(_Meta.TIMESTAMP, ""),
                user_input=meta.get(_Meta.USER_INPUT, doc),
                assistant_reply=meta.get(_Meta.ASSISTANT_REPLY, ""),
                played_song_name=meta.get(_Meta.SONG_NAME) or None,
                played_song_artist=meta.get(_Meta.SONG_ARTIST) or None,
                mood_tag=meta.get(_Meta.MOOD_TAG) or None,
                weather_tag=meta.get(_Meta.WEATHER_TAG) or None,
                time_of_day=meta.get(_Meta.TIME_OF_DAY, "unknown"),
                genre_tag=meta.get(_Meta.GENRE_TAG) or None,
                similarity_score=similarity,
            ))

        return snapshots

    def _metadata_list_to_snapshots(
        self,
        ids: list[str],
        metadatas: list[dict[str, Any]],
        documents: list[str] | None,
    ) -> list[EpisodicSnapshot]:
        """将 ChromaDB get() 的平铺结果映射为 EpisodicSnapshot 列表。"""
        snapshots: list[EpisodicSnapshot] = []
        docs = documents or []

        for i, chroma_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc = docs[i] if i < len(docs) else ""

            try:
                row_id = int(chroma_id)
            except (ValueError, TypeError):
                row_id = 0

            snapshots.append(EpisodicSnapshot(
                id=row_id,
                timestamp=meta.get(_Meta.TIMESTAMP, ""),
                user_input=meta.get(_Meta.USER_INPUT, doc),
                assistant_reply=meta.get(_Meta.ASSISTANT_REPLY, ""),
                played_song_name=meta.get(_Meta.SONG_NAME) or None,
                played_song_artist=meta.get(_Meta.SONG_ARTIST) or None,
                mood_tag=meta.get(_Meta.MOOD_TAG) or None,
                weather_tag=meta.get(_Meta.WEATHER_TAG) or None,
                time_of_day=meta.get(_Meta.TIME_OF_DAY, "unknown"),
                genre_tag=meta.get(_Meta.GENRE_TAG) or None,
                similarity_score=None,
            ))

        return snapshots

    # ================================================================
    # Fallback 查询（ChromaDB 不可用时降级到 SQLite）
    # ================================================================

    async def _fallback_keyword_query(
        self, query_text: str, limit: int, session_id: str | None = None
    ) -> list[EpisodicSnapshot]:
        """ChromaDB 查询失败时的降级方案 —— SQLite LIKE 搜索。"""
        # 取查询文本中的关键词（简单分词：取前 3 个长度 >= 2 的 token）
        tokens = [t for t in query_text.split() if len(t) >= 2][:3]
        if not tokens:
            return await self.query_recent(limit=limit, session_id=session_id)

        # 用第一个有效 token 做 LIKE 搜索
        return await self.query_by_keyword(tokens[0], limit=limit, session_id=session_id)

    async def _fallback_tags_query(
        self,
        mood_tag: str | None,
        weather_tag: str | None,
        time_of_day: str | None,
        genre_tag: str | None,
        limit: int,
        session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        """ChromaDB 标签查询失败时的降级方案 —— 回退到 SQLite 精确过滤。

        这是旧版 query_by_tags() 的 SQLite 实现，完整保留。
        """
        conditions: list[str] = []
        params: list[Any] = []

        if mood_tag:
            conditions.append("mood_tag = ?")
            params.append(mood_tag)
        if weather_tag:
            conditions.append("weather_tag = ?")
            params.append(weather_tag)
        if time_of_day:
            conditions.append("time_of_day = ?")
            params.append(time_of_day)
        if genre_tag:
            conditions.append("genre_tag = ?")
            params.append(genre_tag)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        if not conditions:
            return await self.query_recent(limit=limit)

        where = " AND ".join(conditions)
        params.append(limit)

        def _query() -> list[EpisodicSnapshot]:
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    f"SELECT * FROM episodes WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                    tuple(params),
                ).fetchall()
                return [_row_to_snapshot(row) for row in rows]

        return await asyncio.to_thread(_query)


# ================================================================
# 旧版兼容函数
# ================================================================

def _row_to_snapshot(row: tuple[Any, ...]) -> EpisodicSnapshot:
    """将 SQLite 行元组映射为 EpisodicSnapshot（与旧版完全兼容）。"""
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
    )
