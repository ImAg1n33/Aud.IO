import pytest
from fastapi import BackgroundTasks

from backend.services.assistant_service import AssistantService


class StubMemoryManager:
    def get_profile(self):
        return {
            "core_taste": ["lofi"],
            "artist_preference": {"liked": ["Artist A"], "disliked": []},
            "mood_bias": {},
            "last_updated": "2026-04-24T00:00:00Z",
        }

    def get_preference_summary(self) -> str:
        return "Preferred genres: lofi."

    async def async_update_profile(self, user_input: str, assistant_reply: str):
        return {"ok": True, "user_input": user_input, "assistant_reply": assistant_reply}


class StubEpisodicMemory:
    async def store_snapshot(self, *args, **kwargs):
        return 1

    async def query_recent(self, limit=10):
        return []


@pytest.fixture
def service(tmp_path):
    svc = AssistantService(
        memory_manager=StubMemoryManager(),
        episodic_db_path=tmp_path / "episodes.db",
    )
    return svc


class TestGenerateReply:
    @pytest.mark.asyncio
    async def test_injects_profile_for_music_intent(self, service, monkeypatch) -> None:
        def fake_call_llm(prompt: str, model: str | None = None):
            assert "lofi" in prompt or "Preferred genres" in prompt
            return {
                "analysis": "ok",
                "answer": "done",
                "actions": [],
                "play_keyword": "",
                "provider": "test",
                "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        # Must use music-related input to trigger UserPreferenceProvider
        reply, prompt = await service.generate_reply("播放一首爵士乐", {"scene": "unit"})
        assert reply["answer"] == "done"
        assert prompt

    @pytest.mark.asyncio
    async def test_chitchat_skips_profile(self, service, monkeypatch) -> None:
        def fake_call_llm(prompt: str, model: str | None = None):
            # Profile should NOT be injected for chitchat
            assert "How to use this profile" not in prompt
            return {
                "analysis": "ok",
                "answer": "hello",
                "actions": [],
                "play_keyword": "",
                "provider": "test",
                "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        reply, prompt = await service.generate_reply("hi", {})
        assert reply["answer"] == "hello"

    @pytest.mark.asyncio
    async def test_records_short_term_memory(self, service, monkeypatch) -> None:
        def fake_call_llm(prompt: str, model: str | None = None):
            return {
                "analysis": "ok",
                "answer": "test answer",
                "actions": [],
                "play_keyword": "",
                "provider": "test",
                "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        await service.generate_reply("hello", {})
        last_msg = service.short_term_memory.get_last_user_message()
        assert last_msg == "hello"

    @pytest.mark.asyncio
    async def test_retries_on_tool_error(self, service, monkeypatch) -> None:
        call_count = {"val": 0}

        def fake_call_llm(prompt: str, model: str | None = None):
            call_count["val"] += 1
            return {
                "analysis": "ok",
                "answer": "playing music",
                "actions": [{"tool": "search_music", "keyword": "test"}],
                "play_keyword": "test",
                "provider": "test",
                "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        # Make ToolExecutor always fail with retry context
        class FakeFailExecutor:
            async def execute_actions(self, actions):
                from backend.tools.base import MusicCopyrightError, ToolResult
                return [
                    ToolResult.fail(
                        MusicCopyrightError("copyright"),
                        data={"name": "Blocked Song"},
                        retry_context="copyright blocked",
                    )
                ]

        service.tool_executor = FakeFailExecutor()

        reply, _ = await service.generate_reply("play something", {})
        # Should retry MAX_RETRIES times, then fall back
        assert call_count["val"] == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_graceful_degradation_after_retries(self, service, monkeypatch) -> None:
        def fake_call_llm(prompt: str, model: str | None = None):
            return {
                "analysis": "ok",
                "answer": "playing",
                "actions": [{"tool": "search_music", "keyword": "test"}],
                "play_keyword": "test",
                "provider": "test",
                "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        class AlwaysCopyrightExecutor:
            async def execute_actions(self, actions):
                from backend.tools.base import MusicCopyrightError, ToolResult
                return [
                    ToolResult.fail(
                        MusicCopyrightError("copyright"),
                        retry_context="blocked",
                    )
                ]

        service.tool_executor = AlwaysCopyrightExecutor()

        reply, _ = await service.generate_reply("play something", {})
        assert "Sorry" in reply["answer"] or "抱歉" in reply["answer"] or "copyright" in reply.get("answer", "")
        assert reply["play_keyword"] == ""
        assert "music" not in reply


class TestScheduleProfileUpdate:
    def test_registers_background_task(self, service) -> None:
        bg = BackgroundTasks()
        service.schedule_profile_update(bg, "hello", {"answer": "ok"})
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func.__name__ == "async_update_profile"
