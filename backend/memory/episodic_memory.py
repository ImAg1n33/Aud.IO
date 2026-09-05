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

from backend.data_config import ensure_data_dirs, get_chroma_path, get_db_path
from backend.memory._chroma_repo import ChromaRepository
from backend.memory._migration import MigrationManager
from backend.memory._sqlite_repo import SqliteRepository
from backend.memory.decay import compute_decayed_score
from backend.memory.embedding import (
    EmbeddingProvider,
    create_embedding_provider,
)
from backend.memory.fusion import rrf_fuse
from backend.memory.models import (
    EpisodicSnapshot,
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

    新增 API:
        query_by_semantic()   —— 基于语义相似度的向量检索
        record_play_feedback() —— 播放反馈闭环：用真实听歌结果校准重要性
        get_feedback_stats()  —— 播放→完成率等推荐质量指标
    """

    # 反馈事件对 importance_score 的校准幅度（0.05-0.98 区间内加减）
    # 语义区分：skip = 当下不想听（-0.15）；disliked = 明确厌恶（-0.3 + 画像联动）
    FEEDBACK_IMPORTANCE_DELTA: dict[str, float] = {
        "song_finished": 0.15,   # 完整听完 = 正反馈
        "song_skipped": -0.15,   # 切歌 = 负反馈
        "song_disliked": -0.3,   # 显式不喜欢 = 强负反馈（另联动画像 disliked）
    }

    def __init__(
        self,
        db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        """初始化情节记忆存储。

        Args:
            db_path: SQLite 数据库文件路径（None = 默认 backend/data/episodes.db）
            embedding_provider: 向量嵌入提供者（None = 根据环境变量自动选择）
        """
        # --- 路径初始化（兼容 str 和 Path） ---
        if db_path is None:
            ensure_data_dirs()
            db_path = get_db_path()
            chroma_path = str(get_chroma_path())
        else:
            db_path = Path(db_path)
            chroma_path = str(db_path.parent / "chroma_episodes")
        self.db_path = Path(db_path)  # 确保是 Path 对象，兼容调用方传 str
        self._sqlite = SqliteRepository(self.db_path)

        # --- Embedding provider ---
        self._embed = embedding_provider or create_embedding_provider()

        # --- ChromaDB (必须在 MigrationManager 之前，v1 迁移需要 collection) ---
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
        song_id = None
        if isinstance(played_song, dict):
            song_name = str(played_song.get("name", "")) or None
            song_artist = str(played_song.get("artist", "")) or None
            song_id = str(played_song.get("song_id", "")) or None

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
            song_id,
        )

        # --- 2) ChromaDB 写入（携带向量嵌入） ---
        await self._chroma.upsert(
            row_id=row_id, user_input=user_input, timestamp=timestamp,
            assistant_reply=assistant_reply, song_name=song_name,
            song_artist=song_artist, mood_tag=mood_tag,
            weather_tag=weather_tag, time_of_day=time_of_day,
            genre_tag=genre_tag, session_id=session_id,
            importance_score=importance_score, song_id=song_id,
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
    # 反馈闭环 —— 用真实听歌结果校准记忆
    # ================================================================

    async def record_play_feedback(
        self,
        session_id: str,
        song_id: str,
        event: str,
        listen_seconds: float | None = None,
    ) -> int | None:
        """记录一次播放反馈事件，并据此校准对应快照的重要性。

        事件:
            song_started  —— 播放开始（仅标记，不调权重）
            song_finished —— 完整播完（正反馈：importance +0.15, play_count +1）
            song_skipped  —— 中途切歌（负反馈：importance -0.15, skip_count +1）
            song_disliked —— 显式不喜欢（强负反馈：-0.3 + dislike_count +1，
                              调用方应联动画像 disliked 写入）
            song_failed   —— 播放失败（仅标记，用户无过错不降权）

        Args:
            session_id: 会话标识（与快照写入一致）
            song_id: 歌曲 ID（快照写入时从 played_song 提取）
            event: 反馈事件名
            listen_seconds: 收听秒数（finished/skipped 时前端上报）

        Returns:
            匹配到的快照 ID；未匹配（无 song_id 的历史快照）返回 None。
        """
        row_id = await asyncio.to_thread(
            self._sqlite.find_latest_by_song, session_id, song_id,
        )
        if row_id is None:
            return None

        delta = self.FEEDBACK_IMPORTANCE_DELTA.get(event)
        await self._sqlite.apply_play_feedback(row_id, event, listen_seconds, delta)

        logger.debug(
            "播放反馈已记录: event=%s, song_id=%s, snapshot=%d, delta=%s",
            event, song_id, row_id, delta,
        )
        return row_id

    async def get_song_info_by_feedback(
        self, session_id: str, song_id: str,
    ) -> dict[str, Any] | None:
        """按会话+歌曲 ID 返回最近一次播放的歌曲信息（dislike → 画像写入用）。"""
        row_id = await asyncio.to_thread(
            self._sqlite.find_latest_by_song, session_id, song_id,
        )
        if row_id is None:
            return None
        return await asyncio.to_thread(self._sqlite.get_song_info, row_id)

    async def get_feedback_stats(
        self, session_id: str | None = None,
    ) -> dict[str, Any]:
        """播放反馈聚合统计 —— 播放→完成率（推荐质量指标）。"""
        return await self._sqlite.get_feedback_stats(session_id=session_id)

    # ================================================================
    # 会话摘要（Reflection, v5）—— 跨会话连续性
    # ================================================================

    async def insert_session_summary(
        self,
        session_id: str,
        summary_text: str,
        topics: list[str],
        song_signals: list[dict[str, Any]],
        turn_count: int,
    ) -> int:
        """持久化一条会话摘要（Reflection 产物）。"""
        return await self._sqlite.insert_session_summary(
            session_id, summary_text, topics, song_signals, turn_count,
        )

    async def query_recent_summaries(
        self, session_id: str, limit: int = 3,
    ) -> list[dict[str, Any]]:
        """返回该会话最近 N 条摘要（新→旧），供 SessionSummaryProvider 注入。"""
        return await self._sqlite.query_recent_summaries(session_id, limit=limit)

    async def count_summaries(self, session_id: str) -> int:
        """该会话已有摘要数（反射触发节流）。"""
        return await self._sqlite.count_summaries(session_id)

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
        """基于混合检索的记忆召回 —— 语义 + 关键词 + RRF 融合 + 衰减重排。

        v0.5 混合检索（RFC: 反馈闭环同批）:
        1. 语义腿: ChromaDB 向量相似度（对"上次那种感觉的"类转述查询有效）
        2. 关键词腿: SQLite LIKE（对原文复述、歌名/艺人名精确召回有效）
        3. RRF 融合两路排名（k=60），按 id 去重
        4. 衰减重排: Ebbinghaus 加权分数排序（与旧版一致）
        ChromaDB 不可用时仍降级到纯 SQLite LIKE（多级降级原则）。

        与旧版 query_by_tags() 的关键区别:
        - 旧版: WHERE mood_tag = 'calm'  —— 只能精确匹配标签
        - 新版: 混合召回 + metadata 精确过滤，语义与关键词互补

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

        # --- 混合召回：语义腿 + 关键词腿 → RRF 融合 ---
        fetch_count = max(limit * 3, 15)
        try:
            semantic_candidates = await self._chroma.semantic_search(
                query_text=query_text, where=where_filter, limit=fetch_count,
            )
            keyword_candidates = await self._sqlite.hybrid_keyword_search(
                query_text=query_text, limit=max(limit * 2, 8),
                session_id=session_id,
            )
            candidates = rrf_fuse(semantic_candidates, keyword_candidates)[:fetch_count]
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


