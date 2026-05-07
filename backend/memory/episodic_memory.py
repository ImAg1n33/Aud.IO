"""Episodic memory — SQLite-backed interaction snapshots with tag-based retrieval."""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EpisodicSnapshot:
    id: int
    timestamp: str
    user_input: str
    assistant_reply: str
    played_song_name: str | None
    played_song_artist: str | None
    mood_tag: str | None
    weather_tag: str | None
    time_of_day: str
    genre_tag: str | None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _time_of_day() -> str:
    hour = datetime.now(timezone.utc).hour + 8  # approximate CST
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    return "night"


class EpisodicMemory:
    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            backend_root = Path(__file__).resolve().parents[1]
            db_path = backend_root / "memory" / "episodes.db"
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
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

    async def store_snapshot(
        self,
        user_input: str,
        assistant_reply: str = "",
        played_song: dict[str, Any] | None = None,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        genre_tag: str | None = None,
    ) -> int:
        song_name = None
        song_artist = None
        if isinstance(played_song, dict):
            song_name = str(played_song.get("name", "")) or None
            song_artist = str(played_song.get("artist", "")) or None

        def _store() -> int:
            with sqlite3.connect(str(self.db_path)) as conn:
                cursor = conn.execute(
                    """INSERT INTO episodes
                       (timestamp, user_input, assistant_reply, played_song_name, played_song_artist,
                        mood_tag, weather_tag, time_of_day, genre_tag)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _utc_now_iso(),
                        user_input,
                        assistant_reply,
                        song_name,
                        song_artist,
                        mood_tag,
                        weather_tag,
                        _time_of_day(),
                        genre_tag,
                    ),
                )
                conn.commit()
                return cursor.lastrowid

        return await asyncio.to_thread(_store)

    async def query_recent(self, limit: int = 10) -> list[EpisodicSnapshot]:
        def _query() -> list[EpisodicSnapshot]:
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    "SELECT * FROM episodes ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [_row_to_snapshot(row) for row in rows]

        return await asyncio.to_thread(_query)

    async def query_by_tags(
        self,
        *,
        mood_tag: str | None = None,
        weather_tag: str | None = None,
        time_of_day: str | None = None,
        genre_tag: str | None = None,
        limit: int = 5,
    ) -> list[EpisodicSnapshot]:
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

    async def query_by_keyword(self, keyword: str, limit: int = 5) -> list[EpisodicSnapshot]:
        pattern = f"%{keyword}%"

        def _query() -> list[EpisodicSnapshot]:
            with sqlite3.connect(str(self.db_path)) as conn:
                rows = conn.execute(
                    """SELECT * FROM episodes
                       WHERE user_input LIKE ? OR assistant_reply LIKE ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (pattern, pattern, limit),
                ).fetchall()
                return [_row_to_snapshot(row) for row in rows]

        return await asyncio.to_thread(_query)


def _row_to_snapshot(row: tuple[Any, ...]) -> EpisodicSnapshot:
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
    )
