"""SQLite 情节记忆仓储 —— 纯数据访问，零业务逻辑。

RFC-008 Step 2: 从 episodic_memory.py 提取所有 SQLite 操作方法。
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from backend.memory.models import (
    EpisodicSnapshot,
    _row_to_snapshot,
    _utc_now_iso,
)


class SqliteRepository:
    """SQLite 情节记忆读写 —— 纯数据访问，零业务逻辑。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    # ── 写入 ──────────────────────────────────────────────────────────

    def insert_snapshot(
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
        importance_score: float = 0.5,
    ) -> int:
        """同步 SQLite 插入，返回自增 ID。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """INSERT INTO episodes
                   (timestamp, user_input, assistant_reply, played_song_name, played_song_artist,
                    mood_tag, weather_tag, time_of_day, genre_tag, session_id,
                    importance_score, access_count, last_accessed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
                (
                    timestamp, user_input, assistant_reply,
                    song_name, song_artist,
                    mood_tag, weather_tag, time_of_day, genre_tag,
                    session_id, importance_score,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    # ── 基本查询 ──────────────────────────────────────────────────────

    async def query_recent(
        self, limit: int = 10, session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
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

    async def query_by_keyword(
        self, keyword: str, limit: int = 5, session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        """文本关键词 LIKE 搜索 —— 在 user_input 和 assistant_reply 中匹配。"""
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

    # ── 统计 ──────────────────────────────────────────────────────────

    async def get_preference_stats(
        self, session_id: str | None = None,
    ) -> dict[str, Any]:
        """基于情节记忆的 SQL 聚合统计 —— 生成数据驱动的用户偏好报表。"""
        where_prefix = "WHERE session_id = ? AND " if session_id else "WHERE "
        params: tuple = (session_id,) if session_id else ()

        def _compute() -> dict[str, Any]:
            with sqlite3.connect(str(self.db_path)) as conn:
                genre_rows = conn.execute(
                    f"""SELECT genre_tag, COUNT(*) as cnt FROM episodes
                       {where_prefix}genre_tag IS NOT NULL AND genre_tag != ''
                       GROUP BY genre_tag ORDER BY cnt DESC LIMIT 8""",
                    params,
                ).fetchall()
                top_genres = [{"genre": row[0], "count": row[1]} for row in genre_rows]

                artist_rows = conn.execute(
                    f"""SELECT played_song_artist, COUNT(*) as cnt FROM episodes
                       {where_prefix}played_song_artist IS NOT NULL AND played_song_artist != ''
                       GROUP BY played_song_artist ORDER BY cnt DESC LIMIT 8""",
                    params,
                ).fetchall()
                top_artists = [{"artist": row[0], "count": row[1]} for row in artist_rows]

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

                total_sql = (
                    "SELECT COUNT(*) FROM episodes WHERE session_id = ?"
                    if session_id else "SELECT COUNT(*) FROM episodes"
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
        """将统计数据格式化为 LLM 可读的 prompt 文本块。"""
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

    # ── 衰减 DB 操作 ──────────────────────────────────────────────────

    def load_decay_fields_batch(
        self, snapshot_ids: list[int],
    ) -> dict[int, tuple[float, int, str | None]]:
        """Efficiently load decay fields for a batch of snapshot IDs."""
        if not snapshot_ids:
            return {}
        with sqlite3.connect(str(self.db_path)) as conn:
            placeholders = ",".join("?" for _ in snapshot_ids)
            rows = conn.execute(
                f"SELECT id, importance_score, access_count, last_accessed "
                f"FROM episodes WHERE id IN ({placeholders})",
                snapshot_ids,
            ).fetchall()
        return {
            row[0]: (
                float(row[1]) if row[1] is not None else 0.5,
                int(row[2]) if row[2] is not None else 0,
                str(row[3]) if row[3] is not None else None,
            )
            for row in rows
        }

    async def record_access(self, snapshot_ids: list[int]) -> None:
        """Increment access_count and update last_accessed."""
        if not snapshot_ids:
            return
        now_iso = _utc_now_iso()

        def _update() -> None:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.executemany(
                    "UPDATE episodes SET access_count = access_count + 1, "
                    "last_accessed = ? WHERE id = ?",
                    [(now_iso, sid) for sid in snapshot_ids],
                )
                conn.commit()

        await asyncio.to_thread(_update)

    # ── Fallback 查询 ─────────────────────────────────────────────────

    async def fallback_keyword_query(
        self, query_text: str, limit: int, session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        """ChromaDB 查询失败时降级到 SQLite LIKE 搜索。"""
        tokens = [t for t in query_text.split() if len(t) >= 2][:3]
        if not tokens:
            return await self.query_recent(limit=limit, session_id=session_id)
        return await self.query_by_keyword(tokens[0], limit=limit, session_id=session_id)

    async def fallback_tags_query(
        self,
        mood_tag: str | None,
        weather_tag: str | None,
        time_of_day: str | None,
        genre_tag: str | None,
        limit: int,
        session_id: str | None = None,
    ) -> list[EpisodicSnapshot]:
        """ChromaDB 标签查询失败时降级到 SQLite 精确过滤。"""
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
