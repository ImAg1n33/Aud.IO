"""播放历史与回放 API 测试（RFC-020 契约 C）。"""

from fastapi.testclient import TestClient

from backend.api import routes_agent
from backend.main import app


def _row(song_id: str = "123") -> dict:
    return {
        "song_id": song_id,
        "name": "晴天",
        "artist": "周杰伦",
        "cover_url": "http://c/1.jpg",
        "album": "叶惠美",
        "duration_ms": 269000,
        "requested_keyword": "周杰伦 晴天",
        "played_at": "2026-09-11T00:00:00Z",
        "session_id": "s1",
    }


def test_history_session_scope(monkeypatch) -> None:
    calls: dict = {}

    async def fake_list(session_id=None, limit=50):
        calls["session_id"] = session_id
        calls["limit"] = limit
        return [_row()]

    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "list_async", fake_list,
    )

    client = TestClient(app)
    response = client.get(
        "/v1/agent/history",
        params={"scope": "session", "session_id": "s1", "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "session"
    assert body["songs"][0]["song_id"] == "123"
    assert calls == {"session_id": "s1", "limit": 10}


def test_history_session_requires_session_id() -> None:
    client = TestClient(app)
    response = client.get("/v1/agent/history", params={"scope": "session"})
    assert response.status_code == 400


def test_history_global(monkeypatch) -> None:
    async def fake_list(session_id=None, limit=50):
        assert session_id is None
        return []

    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "list_async", fake_list,
    )
    client = TestClient(app)
    response = client.get("/v1/agent/history")
    assert response.status_code == 200
    assert response.json()["scope"] == "global"


def test_replay_ok(monkeypatch) -> None:
    added: dict = {}

    async def fake_find(song_id, session_id=None):
        assert song_id == "123"
        return _row()

    async def fake_add(session_id, song, requested_keyword=""):
        added["session_id"] = session_id
        added["song_id"] = song["song_id"]
        return 1

    async def fake_mp3(song_id, level="standard"):
        return "http://x/123.mp3"

    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "find_async", fake_find,
    )
    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "add_async", fake_add,
    )
    monkeypatch.setattr(routes_agent, "get_song_mp3_url", fake_mp3)

    client = TestClient(app)
    response = client.post(
        "/v1/agent/replay", json={"song_id": "123", "session_id": "s1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mp3_url"] == "http://x/123.mp3"
    assert body["cover_url"] == "http://c/1.jpg"
    assert added["song_id"] == "123"


def test_replay_not_in_history(monkeypatch) -> None:
    async def fake_find(song_id, session_id=None):
        return None

    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "find_async", fake_find,
    )
    client = TestClient(app)
    response = client.post("/v1/agent/replay", json={"song_id": "404"})
    assert response.status_code == 404


def test_replay_mp3_failure(monkeypatch) -> None:
    async def fake_find(song_id, session_id=None):
        return _row()

    async def fake_mp3(song_id, level="standard"):
        raise RuntimeError("netease down")

    monkeypatch.setattr(
        routes_agent.assistant_service.play_history, "find_async", fake_find,
    )
    monkeypatch.setattr(routes_agent, "get_song_mp3_url", fake_mp3)

    client = TestClient(app)
    response = client.post("/v1/agent/replay", json={"song_id": "123"})
    assert response.status_code == 502


def test_replay_illegal_session_returns_400(monkeypatch) -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/agent/replay",
        json={"song_id": "123", "session_id": "../../etc/passwd"},
    )
    assert response.status_code == 400
