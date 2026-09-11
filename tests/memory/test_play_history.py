"""play_history 仓储测试（RFC-020 契约 B）—— 去重 / 上限 / 查询。"""

import pytest

from backend.memory.play_history import (
    GLOBAL_CAP,
    SESSION_CAP,
    PlayHistoryRepository,
)


@pytest.fixture
def repo(tmp_path):
    r = PlayHistoryRepository(tmp_path / "episodes.db")
    r.ensure_table()
    return r


def _song(song_id: str, name: str | None = None) -> dict:
    return {
        "song_id": song_id,
        "name": name or f"Song {song_id}",
        "artist": "Artist",
        "cover_url": f"http://c/{song_id}.jpg",
        "album": "Album",
        "duration_ms": 200000,
    }


class TestPlayHistoryRepository:
    def test_add_and_list(self, repo) -> None:
        repo.add("s1", _song("1"))
        repo.add("s1", _song("2"))
        rows = repo.list("s1")
        assert [r["song_id"] for r in rows] == ["2", "1"]
        assert rows[0]["cover_url"] == "http://c/2.jpg"
        assert rows[0]["duration_ms"] == 200000

    def test_dedupe_moves_to_top(self, repo) -> None:
        repo.add("s1", _song("1"))
        repo.add("s1", _song("2"))
        repo.add("s1", _song("1"))
        assert [r["song_id"] for r in repo.list("s1")] == ["1", "2"]

    def test_session_isolation(self, repo) -> None:
        repo.add("s1", _song("1"))
        repo.add("s2", _song("2"))
        assert [r["song_id"] for r in repo.list("s1")] == ["1"]
        assert [r["song_id"] for r in repo.list("s2")] == ["2"]

    def test_global_list(self, repo) -> None:
        repo.add("s1", _song("1"))
        repo.add("s2", _song("2"))
        assert [r["song_id"] for r in repo.list(None)] == ["2", "1"]

    def test_session_cap(self, repo) -> None:
        for i in range(SESSION_CAP + 5):
            repo.add("s1", _song(str(i)))
        rows = repo.list("s1", limit=100)
        assert len(rows) == SESSION_CAP
        assert rows[0]["song_id"] == str(SESSION_CAP + 4)

    def test_global_cap(self, repo) -> None:
        for i in range(10):
            for j in range(12):
                repo.add(f"s{i}", _song(f"{i}-{j}"))
        rows = repo.list(None, limit=GLOBAL_CAP)
        assert len(rows) == GLOBAL_CAP

    def test_find_prefers_session(self, repo) -> None:
        repo.add("s1", _song("1"))
        row = repo.find("1", "s1")
        assert row is not None and row["song_id"] == "1"
        assert repo.find("missing") is None

    def test_add_requires_ids(self, repo) -> None:
        assert repo.add("", _song("1")) is None
        assert repo.add("s1", {"song_id": ""}) is None

    @pytest.mark.asyncio
    async def test_async_wrappers(self, repo) -> None:
        await repo.add_async("s1", _song("9"))
        rows = await repo.list_async("s1")
        assert rows[0]["song_id"] == "9"
        found = await repo.find_async("9", "s1")
        assert found is not None
