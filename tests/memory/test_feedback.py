"""反馈闭环测试 —— 播放事件如何校准记忆重要性（RFC: 反馈闭环）。

覆盖: migration v3 字段、song_id 持久化、四类反馈事件的权重校准、匹配规则。
"""

import sqlite3

import pytest

from backend.memory.episodic_memory import EpisodicMemory


@pytest.fixture
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(db_path=tmp_path / "episodes.db")


class TestFeedbackMigration:
    def test_schema_version_is_6(self, episodic, tmp_path) -> None:
        with sqlite3.connect(str(tmp_path / "episodes.db")) as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            assert row[0] >= 6

    def test_repair_heals_partial_migration(self, tmp_path) -> None:
        """v4 自愈：缺 last_accessed 的历史库补齐后 INSERT 可用。"""
        import sqlite3

        from backend.memory._migration import MigrationManager

        db = tmp_path / "episodes.db"
        # 模拟历史库：已有 v2 部分列但缺 last_accessed，schema_version 记为 2
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "CREATE TABLE episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp TEXT NOT NULL, user_input TEXT NOT NULL, "
                "assistant_reply TEXT NOT NULL DEFAULT '', played_song_name TEXT, "
                "played_song_artist TEXT, mood_tag TEXT, weather_tag TEXT, "
                "time_of_day TEXT NOT NULL DEFAULT 'unknown', genre_tag TEXT, "
                "session_id TEXT NOT NULL DEFAULT 'default', "
                "importance_score REAL NOT NULL DEFAULT 0.5, "
                "access_count INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO schema_version VALUES (2, '2026-01-01T00:00:00Z')")
            conn.commit()

        mgr = MigrationManager(db)
        mgr.initialize_tables()
        assert mgr.get_version() == 6

        with sqlite3.connect(str(db)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
            assert "last_accessed" in cols
            conn.execute(
                "INSERT INTO episodes (timestamp, user_input) VALUES ('t', 'hello')"
            )  # INSERT 不再报错

    def test_feedback_columns_exist(self, episodic, tmp_path) -> None:
        with sqlite3.connect(str(tmp_path / "episodes.db")) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(episodes)")]
        for col in ("song_id", "played_to_completion", "listen_duration",
                    "play_count", "skip_count", "last_feedback"):
            assert col in cols, f"缺少 v3 字段: {col}"

    def test_song_index_exists(self, episodic, tmp_path) -> None:
        with sqlite3.connect(str(tmp_path / "episodes.db")) as conn:
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_episodes_song'"
            ).fetchone()
            assert idx is not None


class TestFeedbackRecording:
    @pytest.mark.asyncio
    async def test_store_snapshot_persists_song_id(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        snap = (await episodic.query_recent())[0]
        assert snap.song_id == "123"

    @pytest.mark.asyncio
    async def test_store_snapshot_without_song_id(self, episodic) -> None:
        await episodic.store_snapshot("推荐点爵士", played_song={"name": "X"})
        snap = (await episodic.query_recent())[0]
        assert snap.song_id is None

    @pytest.mark.asyncio
    async def test_finished_is_positive_feedback(self, episodic) -> None:
        row_id = await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        matched = await episodic.record_play_feedback(
            "default", "123", "song_finished", listen_seconds=180,
        )
        assert matched == row_id

        snap = (await episodic.query_recent())[0]
        assert snap.played_to_completion == 1
        assert snap.play_count == 1
        assert snap.listen_duration == 180
        assert snap.last_feedback == "finished"
        # 播放快照自动 importance=0.8，完整听完 → +0.15
        assert snap.importance_score == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_skipped_is_negative_feedback(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        await episodic.record_play_feedback(
            "default", "123", "song_skipped", listen_seconds=12,
        )

        snap = (await episodic.query_recent())[0]
        assert snap.skip_count == 1
        assert snap.listen_duration == 12
        assert snap.last_feedback == "skipped"
        assert snap.played_to_completion == 0
        assert snap.importance_score == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_started_only_marks(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        await episodic.record_play_feedback("default", "123", "song_started")

        snap = (await episodic.query_recent())[0]
        assert snap.last_feedback == "started"
        assert snap.importance_score == pytest.approx(0.8)  # 权重不变
        assert snap.play_count == 0
        assert snap.skip_count == 0

    @pytest.mark.asyncio
    async def test_failed_does_not_penalize(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        await episodic.record_play_feedback("default", "123", "song_failed")

        snap = (await episodic.query_recent())[0]
        assert snap.last_feedback == "failed"
        assert snap.importance_score == pytest.approx(0.8)  # 用户无过错，不降权
        assert snap.skip_count == 0

    @pytest.mark.asyncio
    async def test_disliked_is_strong_negative(self, episodic) -> None:
        """显式不喜欢 = 强负反馈（-0.3）+ dislike_count，区别于 skip（-0.15）。"""
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        await episodic.record_play_feedback("default", "123", "song_disliked")

        snap = (await episodic.query_recent())[0]
        assert snap.last_feedback == "disliked"
        assert snap.dislike_count == 1
        assert snap.importance_score == pytest.approx(0.5)  # 0.8 - 0.3

    @pytest.mark.asyncio
    async def test_get_song_info_by_feedback(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        info = await episodic.get_song_info_by_feedback("default", "123")
        assert info == {"song_id": "123", "name": "X", "artist": "Y"}
        assert await episodic.get_song_info_by_feedback("default", "999") is None

    @pytest.mark.asyncio
    async def test_unmatched_song_returns_none(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        matched = await episodic.record_play_feedback("default", "999999", "song_finished")
        assert matched is None

    @pytest.mark.asyncio
    async def test_feedback_matches_latest_play(self, episodic) -> None:
        await episodic.store_snapshot(
            "第一遍", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        row_id2 = await episodic.store_snapshot(
            "第二遍", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        matched = await episodic.record_play_feedback("default", "123", "song_finished")
        assert matched == row_id2

    @pytest.mark.asyncio
    async def test_feedback_session_isolation(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
            session_id="alice",
        )
        matched = await episodic.record_play_feedback("bob", "123", "song_finished")
        assert matched is None


class TestImportanceClamping:
    @pytest.mark.asyncio
    async def test_importance_clamped_upper(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        for _ in range(3):  # 0.8 + 0.15*3 = 1.25 → clamp 0.98
            await episodic.record_play_feedback("default", "123", "song_finished")
        snap = (await episodic.query_recent())[0]
        assert snap.importance_score == pytest.approx(0.98)

    @pytest.mark.asyncio
    async def test_importance_clamped_lower(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        for _ in range(8):  # 0.8 - 0.15*8 = -0.4 → clamp 0.05
            await episodic.record_play_feedback("default", "123", "song_skipped")
        snap = (await episodic.query_recent())[0]
        assert snap.importance_score == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_feedback_stats(self, episodic) -> None:
        await episodic.store_snapshot(
            "放首测试歌", played_song={"song_id": "123", "name": "X", "artist": "Y"},
        )
        await episodic.store_snapshot(
            "再来一首", played_song={"song_id": "456", "name": "Z", "artist": "W"},
        )
        await episodic.record_play_feedback("default", "123", "song_finished")
        await episodic.record_play_feedback("default", "123", "song_finished")
        await episodic.record_play_feedback("default", "456", "song_skipped")

        stats = await episodic.get_feedback_stats(session_id="default")
        assert stats["total_finished"] == 2
        assert stats["total_skipped"] == 1
        assert stats["completion_rate"] == pytest.approx(2 / 3)