import pytest

from backend.memory.episodic_memory import EpisodicMemory


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
