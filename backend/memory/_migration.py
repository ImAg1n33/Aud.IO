"""版本化数据库迁移管理器 —— SQLite schema + ChromaDB 元数据 backfill。

RFC-008 Step 3: 从 episodic_memory.py 提取。
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend.memory.models import _Meta, _utc_now_iso

logger = logging.getLogger(__name__)


class MigrationManager:
    """版本化数据库迁移 —— SQLite schema 演进 + ChromaDB 元数据回填。

    迁移按版本号递增顺序执行。每个迁移是幂等的（可安全重跑）。
    ChromaDB collection 引用是可选的 —— 仅在需要元数据回填时传入。
    """

    CURRENT_VERSION = 6

    def __init__(
        self,
        db_path: Path,
        chroma_collection: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._collection = chroma_collection
        self._migrations = [
            (1, self._migrate_v1_session_id),
            (2, self._migrate_v2_decay_fields),
            (3, self._migrate_v3_feedback_fields),
            (4, self._migrate_v4_repair_columns),
            (5, self._migrate_v5_session_summaries),
            (6, self._migrate_v6_dislike_count),
        ]

    # ── 初始化 ─────────────────────────────────────────────────────────

    def initialize_tables(self) -> None:
        """Create base tables and indices if they don't exist."""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)
            conn.commit()

        self.run_pending()

    # ── 版本查询 ───────────────────────────────────────────────────────

    def get_version(self) -> int:
        """Return the highest applied migration version, or 0 if none."""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                row = conn.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()
                return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def _record_version(self, version: int) -> None:
        """Record a migration as applied."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _utc_now_iso()),
            )
            conn.commit()
        logger.info("Migration v%d applied successfully.", version)

    def run_pending(self) -> None:
        """Execute all pending migrations in version order."""
        current = self.get_version()
        for version, migrate_fn in self._migrations:
            if version > current:
                migrate_fn()
                self._record_version(version)

    # ── v1: 多用户隔离 ─────────────────────────────────────────────────

    def _migrate_v1_session_id(self) -> None:
        """Add session_id column for multi-user isolation."""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute(
                    "ALTER TABLE episodes ADD COLUMN session_id "
                    "TEXT NOT NULL DEFAULT 'default'"
                )
                conn.commit()
        except sqlite3.OperationalError:
            pass

        # ChromaDB backfill
        if self._collection is None:
            return
        try:
            results = self._collection.get(include=["metadatas"])
            if results["ids"]:
                ids_to_update: list[str] = []
                metadatas_to_update: list[dict[str, str]] = []
                for i, cid in enumerate(results["ids"]):
                    meta = results["metadatas"][i] if i < len(results["metadatas"]) else {}
                    if _Meta.SESSION_ID not in meta or not meta[_Meta.SESSION_ID]:
                        meta[_Meta.SESSION_ID] = "default"
                        ids_to_update.append(cid)
                        metadatas_to_update.append(meta)
                if ids_to_update:
                    self._collection.update(
                        ids=ids_to_update, metadatas=metadatas_to_update,
                    )
                    logger.info(
                        "ChromaDB backfill: session_id for %d entries",
                        len(ids_to_update),
                    )
        except Exception as exc:
            logger.warning("ChromaDB session_id backfill skipped: %s", exc)

    # ── v2: 记忆衰减字段 ───────────────────────────────────────────────

    def _migrate_v2_decay_fields(self) -> None:
        """Add importance_score, access_count, last_accessed for memory decay."""
        with sqlite3.connect(str(self.db_path)) as conn:
            for col, col_type in [
                ("importance_score", "REAL NOT NULL DEFAULT 0.5"),
                ("access_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_accessed", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE episodes ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    # ── v3: 播放反馈字段（RFC: 反馈闭环） ────────────────────────────────

    def _migrate_v3_feedback_fields(self) -> None:
        """Add song_id + playback feedback columns for the feedback loop.

        - song_id: NetEase song ID, used to match feedback events to snapshots
        - played_to_completion / play_count / skip_count / last_feedback / listen_duration:
          implicit signals that calibrate importance_score (DJ learns from outcomes)
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            for col, col_type in [
                ("song_id", "TEXT"),
                ("played_to_completion", "INTEGER NOT NULL DEFAULT 0"),
                ("listen_duration", "REAL"),
                ("play_count", "INTEGER NOT NULL DEFAULT 0"),
                ("skip_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_feedback", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE episodes ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodes_song "
                    "ON episodes(session_id, song_id)"
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()

    # ── v4: 自愈修复 ─────────────────────────────────────────────────

    def _migrate_v4_repair_columns(self) -> None:
        """确保所有期望列存在（历史库迁移状态不完整时的兜底）。

        背景: 早期镜像时代的 episodes 表可能已含部分 v2 列（importance_score /
        access_count）但缺 last_accessed，且 schema_version 已被记为 2/3，
        导致 v2/v3 迁移被跳过而列缺失 → store_snapshot INSERT 报错。
        本迁移幂等地补齐所有缺失列，任何历史状态都能自愈。
        """
        expected: dict[str, str] = {
            "session_id": "TEXT NOT NULL DEFAULT 'default'",
            "importance_score": "REAL NOT NULL DEFAULT 0.5",
            "access_count": "INTEGER NOT NULL DEFAULT 0",
            "last_accessed": "TEXT",
            "song_id": "TEXT",
            "played_to_completion": "INTEGER NOT NULL DEFAULT 0",
            "listen_duration": "REAL",
            "play_count": "INTEGER NOT NULL DEFAULT 0",
            "skip_count": "INTEGER NOT NULL DEFAULT 0",
            "last_feedback": "TEXT",
        }
        with sqlite3.connect(str(self.db_path)) as conn:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
            repaired: list[str] = []
            for col, col_type in expected.items():
                if col in existing:
                    continue
                try:
                    conn.execute(
                        f"ALTER TABLE episodes ADD COLUMN {col} {col_type}"
                    )
                    repaired.append(col)
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        if repaired:
            logger.info("v4 自愈修复: 补齐缺失列 %s", repaired)

    # ── v5: 会话摘要（Reflection） ────────────────────────────────────

    def _migrate_v5_session_summaries(self) -> None:
        """Create session_summaries table for cross-session continuity.

        Reflection 每 N 轮把短时对话压成结构化摘要存这里，
        下次会话启动时由 SessionSummaryProvider 注入 —— DJ 跨会话不失忆。
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    topics TEXT NOT NULL DEFAULT '[]',
                    song_signals TEXT NOT NULL DEFAULT '[]',
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_summaries_session
                ON session_summaries(session_id, id DESC)
            """)
            conn.commit()

    # ── v6: 显式不喜欢计数（拒绝学习） ────────────────────────────────

    def _migrate_v6_dislike_count(self) -> None:
        """Add dislike_count for explicit dislike feedback (v0.6 拒绝学习).

        与 skip 的语义区分：skip = 当下不想听这首（-0.15）；
        disliked = 明确厌恶这首/该艺人（-0.3 + 写入画像 disliked）。
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            try:
                conn.execute(
                    "ALTER TABLE episodes ADD COLUMN dislike_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
            conn.commit()
