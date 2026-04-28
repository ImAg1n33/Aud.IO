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

    def fake_call_llm(prompt: str, model: str | None = None):
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


def test_generate_reply_retries_on_unplayable_music(monkeypatch) -> None:
    prompts: list[str] = []

    def fake_build_prompt(user_input: str, context: dict):
        prompts.append(user_input)
        return "PROMPT"

    def fake_call_llm(prompt: str, model: str | None = None):
        assert prompt == "PROMPT"
        return {
            "analysis": "analysis",
            "answer": "answer",
            "actions": [{"type": "play_music", "keyword": "kw"}],
            "play_keyword": "kw",
            "provider": "deepseek",
            "model": "deepseek-chat",
        }

    attach_results = iter(
        [
            {
                "analysis": "analysis",
                "answer": "answer",
                "actions": [{"type": "play_music", "keyword": "kw"}],
                "play_keyword": "kw",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "music": {"requested_keyword": "kw", "error": "No playable url found for song_id: 1"},
            },
            {
                "analysis": "analysis",
                "answer": "ok",
                "actions": [],
                "play_keyword": "",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "music": {
                    "requested_keyword": "safe",
                    "song_id": "2",
                    "name": "Safe Song",
                    "artist": "Safe Artist",
                    "mp3_url": "https://example.com/a.mp3",
                },
            },
        ]
    )

    monkeypatch.setattr(assistant_service_module, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(assistant_service_module, "call_llm", fake_call_llm)

    service = AssistantService(memory_manager=StubMemoryManager())
    monkeypatch.setattr(service, "_attach_music_result", lambda reply: next(attach_results))

    reply, _ = service.generate_reply("来点歌", {"scene": "unit"})

    assert reply["music"]["name"] == "Safe Song"
    assert len(prompts) == 2
    assert "系统提示：刚才你推荐的歌曲" in prompts[1]


def test_generate_reply_graceful_degradation_after_retries(monkeypatch) -> None:
    call_count = {"value": 0}

    def fake_build_prompt(user_input: str, context: dict):
        return "PROMPT"

    def fake_call_llm(prompt: str, model: str | None = None):
        call_count["value"] += 1
        return {
            "analysis": "analysis",
            "answer": "answer",
            "actions": [{"type": "play_music", "keyword": "kw"}],
            "play_keyword": "kw",
            "provider": "deepseek",
            "model": "deepseek-chat",
        }

    def always_error(reply: dict):
        return {
            "analysis": "analysis",
            "answer": "answer",
            "actions": [{"type": "play_music", "keyword": "kw"}],
            "play_keyword": "kw",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "music": {"requested_keyword": "kw", "error": "No playable url found for song_id: 1"},
        }

    monkeypatch.setattr(assistant_service_module, "build_prompt", fake_build_prompt)
    monkeypatch.setattr(assistant_service_module, "call_llm", fake_call_llm)

    service = AssistantService(memory_manager=StubMemoryManager())
    monkeypatch.setattr(service, "_attach_music_result", always_error)

    reply, _ = service.generate_reply("来点歌", {"scene": "unit"})

    assert call_count["value"] == 3
    assert "抱歉，我为您连续挑选了几首歌" in reply["answer"]
    assert reply["say"] == reply["answer"]
    assert reply["play_keyword"] == ""
    assert "music" not in reply
