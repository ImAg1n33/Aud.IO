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
from pathlib import Path
from typing import Any

from backend.memory._chroma_repo import ChromaRepository
from backend.memory._migration import MigrationManager
from backend.memory._sqlite_repo import SqliteRepository
from backend.memory.decay import compute_decayed_score
from backend.memory.embedding import (
    EmbeddingProvider,
    create_embedding_provider,
)
from backend.memory.models import (
    EpisodicSnapshot,
    _Meta,
    _time_of_day,
    _utc_now_iso,
)
from backend.memory.mood_detector import MoodDetector

logger = logging.getLogger(__name__)


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
        self._sqlite = SqliteRepository(self.db_path)

        # --- Embedding provider ---
        self._embed = embedding_provider or create_embedding_provider()

        # --- ChromaDB (必须在 MigrationManager 之前，v1 迁移需要 collection) ---
        chroma_path = str(self.db_path.parent / "chroma_episodes")
        self._chroma = ChromaRepository(chroma_path, self._embed)
        self._chroma.initialize()

        # --- Migration (needs both db_path and _collection) ---
        self._migration = MigrationManager(self.db_path, self._chroma.collection)
        self._migration.initialize_tables()

    # ── Backward-compat delegation for tests ───────────────────────────

    def _get_schema_version(self) -> int:
        return self._migration.get_version()

    def _run_migrations(self) -> None:
        self._migration.run_pending()

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
        importance_score: float | None = None,
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
            importance_score: 记忆重要性（None = 自动推断:
                歌曲播放→0.8, 推荐→0.6, 闲聊→0.3）

        Returns:
            新插入记录的 ID（与 SQLite episodes.id 一致）
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

        # --- 自动推断重要性评分 ---
        if importance_score is None:
            if played_song:
                importance_score = 0.8  # 播放了歌曲 — 高价值记忆
            elif mood_tag:
                importance_score = 0.6  # 有情绪信号的推荐
            else:
                importance_score = 0.3  # 普通闲聊

        time_of_day = _time_of_day()
        timestamp = _utc_now_iso()

        # --- 1) SQLite 写入（获取自增 ID） ---
        row_id = await asyncio.to_thread(
            self._sqlite.insert_snapshot,
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
            importance_score,
        )

        # --- 2) ChromaDB 写入（携带向量嵌入） ---
        await self._chroma.upsert(
            row_id=row_id, user_input=user_input, timestamp=timestamp,
            assistant_reply=assistant_reply, song_name=song_name,
            song_artist=song_artist, mood_tag=mood_tag,
            weather_tag=weather_tag, time_of_day=time_of_day,
            genre_tag=genre_tag, session_id=session_id,
            importance_score=importance_score,
        )

        logger.debug(
            "情节记忆已存储: id=%d, mood=%s, importance=%.1f, time=%s, input=%.60s...",
            row_id, mood_tag or "(none)", importance_score, time_of_day, user_input,
        )

        return row_id

    # ================================================================
    # 记忆衰减 — DB 操作（委托到 SqliteRepository）
    # ================================================================

    def _load_decay_fields_batch(
        self, snapshot_ids: list[int],
    ) -> dict[int, tuple[float, int, str | None]]:
        return self._sqlite.load_decay_fields_batch(snapshot_ids)

    async def record_access(self, snapshot_ids: list[int]) -> None:
        await self._sqlite.record_access(snapshot_ids)

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
            按衰减加权分数降序排列的 EpisodicSnapshot 列表，
            每个 snapshot 的 similarity_score 字段包含原始余弦相似度。
        """
        # --- 构建 ChromaDB where 过滤条件 ---
        where_filter = ChromaRepository.build_where(
            mood_tag=mood_tag, time_of_day=time_of_day,
            genre_tag=genre_tag, session_id=session_id,
        )

        # --- ChromaDB 向量检索（拉取更多候选供衰减重排） ---
        fetch_count = max(limit * 3, 15)
        try:
            candidates = await self._chroma.semantic_search(
                query_text=query_text, where=where_filter, limit=fetch_count,
            )
        except Exception as exc:
            logger.error("ChromaDB 查询失败，fallback 到 SQLite: %s", exc)
            return await self._fallback_keyword_query(query_text, limit, session_id)

        # --- 加载衰减字段并重排序 ---
        if candidates:
            ids = [s.id for s in candidates]
            decay_data = self._load_decay_fields_batch(ids)
            now_iso = _utc_now_iso()

            for snap in candidates:
                imp, acc, last_acc = decay_data.get(
                    snap.id, (snap.importance_score, snap.access_count, snap.last_accessed),
                )
                snap.importance_score = imp
                snap.access_count = acc
                snap.last_accessed = last_acc
                raw_sim = snap.similarity_score or 0.5
                snap.similarity_score = raw_sim  # keep original cosine
                # Attach decayed score as a transient attribute for sorting
                snap._decayed_score = compute_decayed_score(
                    semantic_sim=raw_sim,
                    importance=imp,
                    access_count=acc,
                    last_accessed=last_acc,
                    created_at=snap.timestamp,
                    now_iso=now_iso,
                )

            candidates.sort(key=lambda s: getattr(s, "_decayed_score", 0.0), reverse=True)

        # --- 截取最终结果 & 记录访问 ---
        final = candidates[:limit]
        if final:
            await self.record_access([s.id for s in final])
        return final

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

        where_filter = ChromaRepository.build_where(
            mood_tag=mood_tag, weather_tag=weather_tag,
            time_of_day=time_of_day, genre_tag=genre_tag,
            session_id=session_id,
        )

        try:
            return self._chroma.tags_search(where=where_filter, limit=limit)
        except Exception as exc:
            logger.error("ChromaDB 标签查询失败，fallback 到 SQLite: %s", exc)
            return await self._fallback_tags_query(
                mood_tag, weather_tag, time_of_day, genre_tag, limit, session_id,
            )

    # ================================================================
    # 读取路径 —— 保留的旧版查询方法
    # ================================================================

    async def query_recent(
        self, limit: int = 10, session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        return await self._sqlite.query_recent(limit=limit, session_id=session_id)

    async def query_by_keyword(self, keyword: str, limit: int = 5, session_id: str | None = None) -> list[EpisodicSnapshot]:
        """文本关键词 LIKE 搜索 —— 在 user_input 和 assistant_reply 中匹配。

        注意：这是传统的 SQL LIKE 搜索，不是语义搜索。
        语义搜索请使用 query_by_semantic()。
        """
        return await self._sqlite.query_by_keyword(
            keyword=keyword, limit=limit, session_id=session_id,
        )

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
        return await self._sqlite.get_preference_stats(session_id=session_id)

    def format_stats_for_prompt(self, stats: dict[str, Any]) -> str | None:
        return self._sqlite.format_stats_for_prompt(stats)

    # ================================================================
    # Fallback 查询（ChromaDB 不可用时降级到 SQLite）
    # ================================================================

    async def _fallback_keyword_query(
        self, query_text: str, limit: int, session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        return await self._sqlite.fallback_keyword_query(
            query_text, limit=limit, session_id=session_id,
        )

    async def _fallback_tags_query(
        self,
        mood_tag: str | None,
        weather_tag: str | None,
        time_of_day: str | None,
        genre_tag: str | None,
        limit: int,
        session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        return await self._sqlite.fallback_tags_query(
            mood_tag=mood_tag, weather_tag=weather_tag,
            time_of_day=time_of_day, genre_tag=genre_tag,
            limit=limit, session_id=session_id,
        )


