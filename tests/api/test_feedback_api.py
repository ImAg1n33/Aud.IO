"""反馈 API 测试 —— POST /v1/agent/feedback 契约。"""

from fastapi.testclient import TestClient

from backend.api import routes_agent
from backend.main import app


def test_feedback_route_ok(monkeypatch) -> None:
    async def fake_record(session_id: str, song_id: str, event: str, listen_seconds=None):
        assert session_id == "s1"
        assert song_id == "123"
        assert event == "song_finished"
        assert listen_seconds == 180
        return 42

    monkeypatch.setattr(
        routes_agent.assistant_service.episodic_memory,
        "record_play_feedback",
        fake_record,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={
            "event": "song_finished",
            "song_id": "123",
            "session_id": "s1",
            "listen_seconds": 180,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "matched_snapshot_id": 42, "disliked_artist": None}


def test_feedback_disliked_writes_profile(monkeypatch) -> None:
    """song_disliked → 强降权 + 确定性写入画像 disliked（拒绝学习全链）。"""
    written: list[str] = []

    async def fake_record(session_id: str, song_id: str, event: str, listen_seconds=None):
        assert event == "song_disliked"
        return 7

    async def fake_song_info(session_id: str, song_id: str):
        return {"song_id": "123", "name": "X", "artist": "讨厌艺人"}

    monkeypatch.setattr(
        routes_agent.assistant_service.episodic_memory,
        "record_play_feedback",
        fake_record,
    )
    monkeypatch.setattr(
        routes_agent.assistant_service.episodic_memory,
        "get_song_info_by_feedback",
        fake_song_info,
    )

    class FakeMgr:
        def add_disliked_artist(self, artist: str) -> bool:
            written.append(artist)
            return True

    ctx = routes_agent.assistant_service.session_manager.get_or_create("dislike-api-test")
    ctx.memory_manager = FakeMgr()

    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_disliked", "song_id": "123", "session_id": "dislike-api-test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matched_snapshot_id"] == 7
    assert body["disliked_artist"] == "讨厌艺人"
    assert written == ["讨厌艺人"]


def test_feedback_unmatched_returns_null(monkeypatch) -> None:
    async def fake_record(session_id: str, song_id: str, event: str, listen_seconds=None):
        return None

    monkeypatch.setattr(
        routes_agent.assistant_service.episodic_memory,
        "record_play_feedback",
        fake_record,
    )

    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_started", "song_id": "999"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "matched_snapshot_id": None, "disliked_artist": None}


def test_feedback_invalid_event_returns_422() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_hacked", "song_id": "123"},
    )
    assert response.status_code == 422


def test_feedback_missing_song_id_returns_422() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_started"},
    )
    assert response.status_code == 422


def test_feedback_negative_listen_seconds_returns_422() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_skipped", "song_id": "123", "listen_seconds": -5},
    )
    assert response.status_code == 422


def test_feedback_illegal_session_returns_400() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/agent/feedback",
        json={"event": "song_started", "song_id": "123", "session_id": "../../etc/passwd"},
    )
    assert response.status_code == 400
    assert "Invalid session_id" in response.json().get("detail", "")