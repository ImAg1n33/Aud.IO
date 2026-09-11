"""播放历史仓储 —— play_history 表（RFC-020 契约 B）。

每次 music 事件发出即写入：
- 会话内去重（重播 → 移除旧行再插入，保持"最近播放"语义）
- 上限：每会话 SESSION_CAP 首、全局 GLOBAL_CAP 首（FIFO 淘汰）
- 只存元数据 —— mp3_url 会过期，播放前必须重新解析

数据用途：会话歌单（列表循环/随机）、跨会话最近播放、自动续播去重。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from backend.memory.models import _utc_now_iso

SESSION_CAP = 50
GLOBAL_CAP = 100

_COLUMNS = (
    "session_id", "song_id", "name", "artist",
    "cover_url", "album", "duration_ms", "requested_keyword", "played_at",
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "song_id": str(row["song_id"]),
        "name": row["name"] or "",
        "artist": row["artist"] or "",
        "cover_url": row["cover_url"] or "",
        "album": row["album"] or "",
        "duration_ms": row["duration_ms"],
        "requested_keyword": row["requested_keyword"] or "",
        "played_at": row["played_at"] or "",
        "session_id": row["session_id"] or "",
    }


class PlayHistoryRepository:
    """play_history 读写 —— 会话歌单缓存 / 跨会话最近播放的数据源。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── schema（app 由 migration v7 建表；独立使用时可显式调用） ─────────

    def ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    song_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    cover_url TEXT NOT NULL DEFAULT '',
                    album TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER,
                    requested_keyword TEXT NOT NULL DEFAULT '',
                    played_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_play_history_session "
                "ON play_history(session_id, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_play_history_song "
                "ON play_history(song_id, id DESC)"
            )
            conn.commit()

    # ── 写入 ──────────────────────────────────────────────────────────

    def add(
        self, session_id: str, song: dict[str, Any],
        requested_keyword: str = "",
    ) -> int | None:
        """写入一次播放（music 事件发出时调用）。

        会话内去重：同一首歌重播 → 删除旧行再插入（成为"最近"）。
        写入后执行两级 FIFO 淘汰（会话 cap / 全局 cap）。
        """
        sid = str(session_id or "").strip()
        song_id = str((song or {}).get("song_id") or "").strip()
        if not sid or not song_id:
            return None

        raw_duration = song.get("duration_ms")
        duration_ms = (
            int(raw_duration)
            if isinstance(raw_duration, (int, float)) and raw_duration > 0
            else None
        )

        values = (
            sid,
            song_id,
            str(song.get("name") or ""),
            str(song.get("artist") or ""),
            str(song.get("cover_url") or ""),
            str(song.get("album") or ""),
            duration_ms,
            str(requested_keyword or song.get("requested_keyword") or ""),
            _utc_now_iso(),
        )

        with self._connect() as conn:
            conn.execute(
                "DELETE FROM play_history WHERE session_id = ? AND song_id = ?",
                (sid, song_id),
            )
            cursor = conn.execute(
                f"INSERT INTO play_history ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                values,
            )
            # 会话上限（保留最新 SESSION_CAP 条）
            conn.execute(
                """DELETE FROM play_history
                   WHERE session_id = ? AND id NOT IN (
                       SELECT id FROM play_history
                       WHERE session_id = ? ORDER BY id DESC LIMIT ?
                   )""",
                (sid, sid, SESSION_CAP),
            )
            # 全局上限（保留最新 GLOBAL_CAP 条）
            conn.execute(
                """DELETE FROM play_history
                   WHERE id NOT IN (
                       SELECT id FROM play_history ORDER BY id DESC LIMIT ?
                   )""",
                (GLOBAL_CAP,),
            )
            conn.commit()
            return cursor.lastrowid

    async def add_async(
        self, session_id: str, song: dict[str, Any],
        requested_keyword: str = "",
    ) -> int | None:
        return await asyncio.to_thread(
            self.add, session_id, song, requested_keyword,
        )

    # ── 查询 ──────────────────────────────────────────────────────────

    def list(
        self, session_id: str | None = None, limit: int = SESSION_CAP,
    ) -> list[dict[str, Any]]:
        """最近播放列表（新→旧）。

        session_id=None → 跨会话全局最近；否则为该会话歌单缓存。
        """
        safe_limit = max(1, min(int(limit), GLOBAL_CAP))

        def _query() -> list[dict[str, Any]]:
            with self._connect() as conn:
                if session_id:
                    rows = conn.execute(
                        "SELECT * FROM play_history WHERE session_id = ? "
                        "ORDER BY id DESC LIMIT ?",
                        (session_id, safe_limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM play_history ORDER BY id DESC LIMIT ?",
                        (safe_limit,),
                    ).fetchall()
                return [_row_to_dict(row) for row in rows]

        return _query()

    async def list_async(
        self, session_id: str | None = None, limit: int = SESSION_CAP,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.list, session_id, limit)

    def find(
        self, song_id: str, session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """按歌曲 ID 找最近一条记录（优先本会话，再回退全局）。"""
        sid = str(song_id or "").strip()
        if not sid:
            return None

        def _query() -> dict[str, Any] | None:
            with self._connect() as conn:
                if session_id:
                    row = conn.execute(
                        "SELECT * FROM play_history "
                        "WHERE song_id = ? AND session_id = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (sid, session_id),
                    ).fetchone()
                    if row is not None:
                        return _row_to_dict(row)
                row = conn.execute(
                    "SELECT * FROM play_history WHERE song_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                return _row_to_dict(row) if row is not None else None

        return _query()

    async def find_async(
        self, song_id: str, session_id: str | None = None,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.find, song_id, session_id)
