import pytest
from fastapi.testclient import TestClient

from backend.api import routes_agent
from backend.main import app


@pytest.mark.asyncio
async def test_agent_respond_route_returns_json(monkeypatch) -> None:
    captured: dict = {}

    async def fake_generate_reply(user_input: str, context: dict | None):
        captured["generated"] = (user_input, context)
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

    def fake_schedule_update(background_tasks, user_input: str, final_reply: dict):
        captured["scheduled"] = (user_input, final_reply)

    monkeypatch.setattr(routes_agent.assistant_service, "generate_reply", fake_generate_reply)
    monkeypatch.setattr(routes_agent.assistant_service, "schedule_profile_update", fake_schedule_update)

    client = TestClient(app)
    response = client.post(
        "/v1/agent/respond",
        json={"user_input": "hello", "context": {"scene": "test"}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"]["answer"] == "done"
    assert payload["prompt"] == "fake-prompt"
    assert captured["generated"][0] == "hello"
    assert captured["scheduled"][0] == "hello"
