from fastapi import BackgroundTasks

import backend.services.assistant_service as assistant_service_module
from backend.services.assistant_service import AssistantService


class StubMemoryManager:
    def get_profile(self):
        return {
            "core_taste": ["lofi"],
            "artist_preference": {"liked": ["Artist A"], "disliked": []},
            "mood_bias": {},
            "last_updated": "2026-04-24T00:00:00Z",
        }

    async def async_update_profile(self, user_input: str, assistant_reply: str):
        return {"ok": True, "user_input": user_input, "assistant_reply": assistant_reply}


def test_generate_reply_injects_profile(monkeypatch) -> None:
    captured: dict = {}

    def fake_build_prompt(user_input: str, context: dict):
        captured["context"] = context
        return "PROMPT"

    def fake_call_llm(prompt: str):
        assert prompt == "PROMPT"
        return {
            "analysis": "analysis",
            "answer": "answer",
            "actions": [],
            "play_keyword": "",
            "provider": "deepseek",
            "model": "deepseek-chat",
        }

    monkeypatch.setattr(assistant_service_module, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(assistant_service_module, "call_llm", fake_call_llm)

    service = AssistantService(memory_manager=StubMemoryManager())
    reply, prompt = service.generate_reply("hi", {"scene": "unit"})

    assert prompt == "PROMPT"
    assert reply["answer"] == "answer"
    assert captured["context"]["scene"] == "unit"
    assert captured["context"]["user_profile"]["core_taste"] == ["lofi"]


def test_schedule_profile_update_registers_background_task() -> None:
    service = AssistantService(memory_manager=StubMemoryManager())
    bg = BackgroundTasks()
    service.schedule_profile_update(bg, "hello", {"answer": "ok"})

    assert len(bg.tasks) == 1
    task = bg.tasks[0]
    assert task.func.__name__ == "async_update_profile"
