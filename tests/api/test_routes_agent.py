import pytest
from fastapi.testclient import TestClient

from backend.api import routes_agent
from backend.main import app


@pytest.mark.asyncio
async def test_agent_respond_route_returns_json(monkeypatch) -> None:
    captured: dict = {}

    async def fake_generate_reply(user_input: str, context: dict | None, session_id: str | None = None):
        captured["generated"] = (user_input, context, session_id)
        return (
            {
                "analysis": "ok",
                "answer": "done",
                "actions": [],
                "play_keyword": "",
                "provider": "deepseek",
                "model": "mock-model",
            },
            "fake-prompt",
        )

    def fake_schedule_update(background_tasks, user_input: str, final_reply: dict, session_id: str | None = None):
        captured["scheduled"] = (user_input, final_reply, session_id)

    monkeypatch.setattr(routes_agent.assistant_service, "generate_reply", fake_generate_reply)
    monkeypatch.setattr(routes_agent.assistant_service, "schedule_profile_update", fake_schedule_update)

    client = TestClient(app)
    response = client.post(
        "/v1/agent/respond",
        json={"user_input": "hello", "context": {"scene": "test"}, "session_id": "s1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"]["answer"] == "done"
    assert payload["prompt"] == "fake-prompt"
    assert captured["generated"][0] == "hello"
    assert captured["generated"][2] == "s1"
    assert captured["scheduled"][0] == "hello"


def test_invalid_session_id_returns_400() -> None:
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    response = client.post(
        "/v1/agent/respond",
        json={"user_input": "hello", "session_id": "../../etc/passwd"},
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "Invalid session_id" in detail
