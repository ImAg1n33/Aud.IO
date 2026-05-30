import sqlite3

import pytest

from backend.memory.episodic_memory import EpisodicMemory, EpisodicSnapshot, _utc_now_iso


@pytest.fixture
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(db_path=tmp_path / "episodes.db")


class TestEpisodicMemory:
    @pytest.mark.asyncio
    async def test_store_and_query_recent(self, episodic) -> None:
        sid = await episodic.store_snapshot(
            "play jazz", "Playing jazz.", mood_tag="calm"
        )
        assert isinstance(sid, int)

        recent = await episodic.query_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].user_input == "play jazz"
        assert recent[0].mood_tag == "calm"

    @pytest.mark.asyncio
    async def test_query_by_tags(self, episodic) -> None:
        await episodic.store_snapshot("happy song", mood_tag="happy", genre_tag="pop")
        await episodic.store_snapshot("sad song", mood_tag="sad", genre_tag="ballad")

        results = await episodic.query_by_tags(mood_tag="happy")
        assert len(results) == 1
        assert results[0].mood_tag == "happy"
        assert results[0].genre_tag == "pop"

    @pytest.mark.asyncio
    async def test_query_by_multiple_tags(self, episodic) -> None:
        await episodic.store_snapshot("rainy jazz", mood_tag="calm", weather_tag="rainy")
        await episodic.store_snapshot("sunny pop", mood_tag="happy", weather_tag="sunny")

        results = await episodic.query_by_tags(mood_tag="calm", weather_tag="rainy")
        assert len(results) == 1
        assert results[0].user_input == "rainy jazz"

    @pytest.mark.asyncio
    async def test_query_by_keyword(self, episodic) -> None:
        await episodic.store_snapshot("play some lofi beats", "Here is lofi.")
        await episodic.store_snapshot("play rock music", "Rock it is.")

        results = await episodic.query_by_keyword("lofi")
        assert len(results) == 1
        assert "lofi" in results[0].user_input

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, episodic) -> None:
        results = await episodic.query_by_tags(mood_tag="nonexistent")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_played_song_stored(self, episodic) -> None:
        await episodic.store_snapshot(
            "play",
            played_song={"name": "Test", "artist": "Artist"},
        )
        recent = await episodic.query_recent()
        assert recent[0].played_song_name == "Test"
        assert recent[0].played_song_artist == "Artist"

    @pytest.mark.asyncio
    async def test_time_of_day_present(self, episodic) -> None:
        await episodic.store_snapshot("play something")
        recent = await episodic.query_recent()
        assert recent[0].time_of_day in {"morning", "afternoon", "evening", "night"}

    @pytest.mark.asyncio
    async def test_limit_respected(self, episodic) -> None:
        for i in range(10):
            await episodic.store_snapshot(f"msg {i}")
        results = await episodic.query_recent(limit=3)
        assert len(results) == 3


class TestPreferenceStats:
    @pytest.mark.asyncio
    async def test_stats_counts_genres(self, episodic) -> None:
        await episodic.store_snapshot("play jazz", genre_tag="jazz",
                                       played_song={"name": "a", "artist": "Artist A"})
        await episodic.store_snapshot("play jazz again", genre_tag="jazz",
                                       played_song={"name": "b", "artist": "Artist A"})
        await episodic.store_snapshot("play rock", genre_tag="rock",
                                       played_song={"name": "c", "artist": "Artist B"})

        stats = await episodic.get_preference_stats()
        assert stats["total_episodes"] == 3
        top_genres = {g["genre"]: g["count"] for g in stats["top_genres"]}
        assert top_genres.get("jazz") == 2
        assert top_genres.get("rock") == 1

    @pytest.mark.asyncio
    async def test_stats_mood_genre_correlation(self, episodic) -> None:
        await episodic.store_snapshot("happy morning", mood_tag="happy", genre_tag="pop")
        await episodic.store_snapshot("happy again", mood_tag="happy", genre_tag="pop")

        stats = await episodic.get_preference_stats()
        correlations = stats["mood_genre_correlations"]
        assert len(correlations) >= 1
        happy_pop = [c for c in correlations if c["mood"] == "happy" and c["genre"] == "pop"]
        assert len(happy_pop) == 1
        assert happy_pop[0]["count"] == 2

    @pytest.mark.asyncio
    async def test_stats_empty_db(self, episodic) -> None:
        stats = await episodic.get_preference_stats()
        assert stats["total_episodes"] == 0
        assert stats["top_genres"] == []

    def test_format_stats_for_prompt(self, episodic) -> None:
        stats = {"total_episodes": 0, "top_genres": [], "top_artists": [],
                 "mood_genre_correlations": [], "time_patterns": []}
        assert episodic.format_stats_for_prompt(stats) is None

        stats["total_episodes"] = 3
        stats["top_genres"] = [{"genre": "jazz", "count": 2}]
        result = episodic.format_stats_for_prompt(stats)
        assert result is not None
        assert "jazz" in result
        assert "2x" in result


class TestMigrationFramework:
    """Verify the versioned migration framework (RFC-007 v0.3)."""

    def test_schema_version_table_exists(self, episodic) -> None:
        """schema_version table is created during init."""
        db_path = str(episodic.db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
        assert row is not None

    def test_current_schema_version_is_2(self, episodic) -> None:
        """Both v1 and v2 migrations are applied."""
        assert episodic._get_schema_version() == 2

    def test_decay_columns_exist(self, episodic) -> None:
        """Migration v2 added the decay columns."""
        db_path = str(episodic.db_path)
        with sqlite3.connect(db_path) as conn:
            cols = conn.execute("PRAGMA table_info(episodes)").fetchall()
            col_names = {c[1] for c in cols}
        for expected in ("importance_score", "access_count", "last_accessed"):
            assert expected in col_names, f"Missing column: {expected}"

    def test_session_id_column_exists(self, episodic) -> None:
        """Migration v1 added session_id."""
        db_path = str(episodic.db_path)
        with sqlite3.connect(db_path) as conn:
            cols = conn.execute("PRAGMA table_info(episodes)").fetchall()
            col_names = {c[1] for c in cols}
        assert "session_id" in col_names

    def test_migration_idempotent(self, episodic) -> None:
        """Re-running init on an already-migrated DB doesn't break."""
        episodic._run_migrations()
        assert episodic._get_schema_version() == 2


class TestMemoryDecay:
    """Verify importance auto-detection, record_access, and decay scoring."""

    @pytest.mark.asyncio
    async def test_song_play_gets_high_importance(self, episodic) -> None:
        """Playing a song → importance 0.8."""
        sid = await episodic.store_snapshot(
            "play jazz",
            played_song={"name": "So What", "artist": "Miles Davis"},
        )
        recent = await episodic.query_recent()
        snap = recent[0]
        assert snap.id == sid
        assert snap.importance_score == 0.8

    @pytest.mark.asyncio
    async def test_chitchat_gets_low_importance(self, episodic) -> None:
        """Bare chitchat (no song, no mood) → importance 0.3."""
        await episodic.store_snapshot("你好")
        recent = await episodic.query_recent()
        assert recent[0].importance_score == 0.3

    @pytest.mark.asyncio
    async def test_mood_signal_gets_medium_importance(self, episodic) -> None:
        """Input with mood signal → importance 0.6 (recommendation-level)."""
        await episodic.store_snapshot("心情低落想听点治愈的歌")
        recent = await episodic.query_recent()
        assert recent[0].importance_score == 0.6

    def test_decay_formula_bounds(self) -> None:
        """Decayed score stays in [0.0, 1.0] range."""
        now = _utc_now_iso()
        score = EpisodicMemory._compute_decayed_score(
            semantic_sim=0.8,
            importance=0.5,
            access_count=0,
            last_accessed=None,
            created_at=now,
            now_iso=now,
        )
        assert 0.0 <= score <= 1.0

    def test_decay_fresh_memory_ranks_higher(self) -> None:
        """A fresh memory scores higher than an old one, all else equal."""
        now = _utc_now_iso()
        # "Old" memory: created 30 days ago, never accessed
        old_score = EpisodicMemory._compute_decayed_score(
            semantic_sim=0.8, importance=0.5, access_count=0,
            last_accessed=None,
            created_at="2026-04-01T00:00:00Z",
            now_iso=now,
        )
        # "Recent" memory: just created
        new_score = EpisodicMemory._compute_decayed_score(
            semantic_sim=0.8, importance=0.5, access_count=0,
            last_accessed=None,
            created_at=now,
            now_iso=now,
        )
        assert new_score > old_score, f"new={new_score:.3f} <= old={old_score:.3f}"

    def test_frequently_accessed_memory_ranks_higher(self) -> None:
        """Access count boosts score."""
        now = _utc_now_iso()
        low = EpisodicMemory._compute_decayed_score(
            semantic_sim=0.8, importance=0.5, access_count=0,
            last_accessed=None, created_at=now, now_iso=now,
        )
        high = EpisodicMemory._compute_decayed_score(
            semantic_sim=0.8, importance=0.5, access_count=10,
            last_accessed=None, created_at=now, now_iso=now,
        )
        assert high > low, f"high={high:.3f} <= low={low:.3f}"

    @pytest.mark.asyncio
    async def test_record_access_increments(self, episodic) -> None:
        """record_access() bumps access_count and sets last_accessed."""
        sid = await episodic.store_snapshot("play jazz")
        await episodic.record_access([sid])

        db_path = str(episodic.db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT access_count, last_accessed FROM episodes WHERE id = ?",
                (sid,),
            ).fetchone()
        assert row[0] == 1
        assert row[1] is not None

    @pytest.mark.asyncio
    async def test_importance_auto_detection_override(self, episodic) -> None:
        """Explicit importance_score overrides auto-detection."""
        await episodic.store_snapshot(
            "hello", importance_score=0.95,
        )
        recent = await episodic.query_recent()
        assert recent[0].importance_score == 0.95

    @pytest.mark.asyncio
    async def test_query_by_semantic_uses_decay_reranking(self, episodic) -> None:
        """query_by_semantic loads decay fields and records access."""
        await episodic.store_snapshot(
            "play some chill jazz", assistant_reply="Playing jazz.",
            played_song={"name": "So What", "artist": "Miles Davis"},
        )
        await episodic.store_snapshot(
            "recommend something calm", assistant_reply="Try this.",
        )

        results = await episodic.query_by_semantic("chill jazz", limit=2)
        assert len(results) >= 1

        # Verify access was recorded for returned snapshots
        db_path = str(episodic.db_path)
        with sqlite3.connect(db_path) as conn:
            for snap in results:
                row = conn.execute(
                    "SELECT access_count FROM episodes WHERE id = ?", (snap.id,),
                ).fetchone()
                assert row is not None and row[0] >= 1
