"""ChromaDB 向量存储仓储 —— 纯数据访问，零业务逻辑。

RFC-008 Step 4: 从 episodic_memory.py 提取所有 ChromaDB 操作方法。
"""

import logging
from typing import Any

from backend.memory.embedding import EmbeddingProvider
from backend.memory.models import EpisodicSnapshot, _Meta

logger = logging.getLogger(__name__)


class ChromaRepository:
    """ChromaDB 向量存储读写 —— 纯数据访问，零业务逻辑。"""

    def __init__(self, chroma_path: str, embed_provider: EmbeddingProvider) -> None:
        self._chroma_path = chroma_path
        self._embed = embed_provider
        self._client: Any = None
        self._collection: Any = None

    # ── 初始化 ─────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """创建 PersistentClient 并获取/创建 episodes collection。"""
        try:
            import chromadb
        except ImportError:
            logger.error(
                "ChromaDB 未安装，无法启用向量检索。"
                "请执行: pip install chromadb"
            )
            raise

        self._client = chromadb.PersistentClient(path=self._chroma_path)
        self._collection = self._client.get_or_create_collection(
            name="episodes",
            metadata={
                "hnsw:space": "cosine",
                "dim": str(self._embed.dimension),
            },
        )
        existing_dim = (self._collection.metadata or {}).get("dim")
        if existing_dim and existing_dim != str(self._embed.dimension):
            logger.warning(
                "向量维度不匹配: collection dim=%s, 当前 provider dim=%s. "
                "运行 `python scripts/rebuild_embeddings.py` 重建索引。",
                existing_dim, self._embed.dimension,
            )
        logger.info(
            "ChromaDB 初始化完成: collection='episodes', path=%s, count=%d",
            self._chroma_path, self._collection.count(),
        )

    @property
    def collection(self) -> Any:
        """Expose collection for MigrationManager ChromaDB backfill."""
        return self._collection

    # ── 写入 ───────────────────────────────────────────────────────────

    async def upsert(
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
        importance_score: float = 0.5,
        song_id: str | None = None,
    ) -> None:
        """向 ChromaDB collection 写入或更新一条记录（携带向量嵌入）。"""
        try:
            embeddings = await self._embed.embed([user_input])
        except Exception as exc:
            logger.error("向量嵌入计算失败，跳过 ChromaDB 写入 (id=%d): %s", row_id, exc)
            return

        metadata: dict[str, str] = {
            _Meta.TIMESTAMP: timestamp,
            _Meta.USER_INPUT: user_input[:_Meta.MAX_TEXT_LEN],
            _Meta.ASSISTANT_REPLY: assistant_reply[:_Meta.MAX_TEXT_LEN],
            _Meta.SONG_NAME: song_name or "",
            _Meta.SONG_ARTIST: song_artist or "",
            _Meta.SONG_ID: song_id or "",
            _Meta.MOOD_TAG: mood_tag or "",
            _Meta.WEATHER_TAG: weather_tag or "",
            _Meta.TIME_OF_DAY: time_of_day,
            _Meta.GENRE_TAG: genre_tag or "",
            _Meta.SESSION_ID: session_id,
            "importance": str(importance_score),
        }

        try:
            self._collection.upsert(
                ids=[str(row_id)],
                embeddings=embeddings,
                documents=[user_input],
                metadatas=[metadata],
            )
        except Exception as exc:
            logger.error("ChromaDB 写入失败 (id=%d): %s", row_id, exc)

    # ── 语义检索 ──────────────────────────────────────────────────────

    async def semantic_search(
        self, query_text: str, where: dict[str, Any] | None, limit: int,
    ) -> list[EpisodicSnapshot]:
        """向量语义检索 —— 返回 EpisodicSnapshot 列表（仅 ChromaDB，无衰减重排）。"""
        try:
            query_embeddings = await self._embed.embed([query_text])
        except Exception as exc:
            logger.error("查询向量嵌入失败: %s", exc)
            raise

        results = self._collection.query(
            query_embeddings=query_embeddings,
            n_results=limit,
            where=where,
            include=["metadatas", "documents", "distances"],
        )
        return self._results_to_snapshots(results)

    # ── 标签检索 ──────────────────────────────────────────────────────

    def tags_search(
        self, where: dict[str, Any] | None, limit: int,
    ) -> list[EpisodicSnapshot]:
        """ChromaDB metadata 标签精确过滤。"""
        results = self._collection.get(
            where=where, limit=limit,
            include=["metadatas", "documents"],
        )
        if not results["ids"]:
            return []

        snapshots = self._metadata_to_snapshots(
            results["ids"], results["metadatas"], results.get("documents"),
        )
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots[:limit]

    # ── Where 条件构建 ────────────────────────────────────────────────

    @staticmethod
    def build_where(
        *,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        time_of_day: str | None = None,
        genre_tag: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """将标签过滤参数转换为 ChromaDB metadata where 过滤条件。"""
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

    # ── 结果映射 (内部) ───────────────────────────────────────────────

    def _results_to_snapshots(self, results: dict[str, Any]) -> list[EpisodicSnapshot]:
        """将 ChromaDB query() 返回结果映射为 EpisodicSnapshot 列表。"""
        ids_list = results.get("ids", [[]])
        metas_list = results.get("metadatas", [[]])
        docs_list = results.get("documents", [[]])
        dists_list = results.get("distances", [[]])

        ids = ids_list[0] if ids_list else []
        metas = metas_list[0] if metas_list else []
        docs = docs_list[0] if docs_list else []
        dists = dists_list[0] if dists_list else []

        snapshots: list[EpisodicSnapshot] = []
        for i, chroma_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            doc = docs[i] if i < len(docs) else ""
            distance = dists[i] if i < len(dists) else None

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

    def _metadata_to_snapshots(
        self, ids: list[str], metadatas: list[dict[str, Any]],
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
