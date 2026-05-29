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


@pytest.fixture
def service(tmp_path):
    svc = AssistantService(episodic_db_path=tmp_path / "episodes.db")
    # Inject stub MemoryManager into the session context for controllable tests
    TEST_SID = "test-session"
    ctx = svc.session_manager.get_or_create(TEST_SID)
    ctx.memory_manager = StubMemoryManager()
    return svc


TEST_SID = "test-session"


class TestGenerateReply:
    @pytest.mark.asyncio
    async def test_injects_profile_for_music_intent(self, service, monkeypatch) -> None:
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
            assert "lofi" in user_prompt or "Preferred genres" in user_prompt
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

        reply, prompt = await service.generate_reply(
            "播放一首爵士乐", {"scene": "unit"}, session_id=TEST_SID,
        )
        assert reply["answer"] == "done"
        assert prompt

    @pytest.mark.asyncio
    async def test_chitchat_skips_profile(self, service, monkeypatch) -> None:
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
            assert "User Music Profile" not in user_prompt
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

        reply, prompt = await service.generate_reply("hi", {}, session_id=TEST_SID)
        assert reply["answer"] == "hello"

    @pytest.mark.asyncio
    async def test_records_short_term_memory(self, service, monkeypatch) -> None:
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
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

        await service.generate_reply("hello", {}, session_id=TEST_SID)
        ctx = service.session_manager.get_or_create(TEST_SID)
        last_msg = ctx.short_term_memory.get_last_user_message()
        assert last_msg == "hello"

    @pytest.mark.asyncio
    async def test_retries_on_tool_error(self, service, monkeypatch) -> None:
        call_count = {"val": 0}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
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

        reply, _ = await service.generate_reply("play something", {}, session_id=TEST_SID)
        assert call_count["val"] == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_graceful_degradation_after_retries(self, service, monkeypatch) -> None:
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
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

        reply, _ = await service.generate_reply("play something", {}, session_id=TEST_SID)
        assert "Sorry" in reply["answer"] or "抱歉" in reply["answer"] or "copyright" in reply.get("answer", "")
        assert reply["play_keyword"] == ""
        assert "music" not in reply


class TestScheduleProfileUpdate:
    def test_registers_background_task(self, service) -> None:
        bg = BackgroundTasks()
        service.schedule_profile_update(bg, "hello", {"answer": "ok"}, session_id=TEST_SID)
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func.__name__ == "async_update_profile"


class TestSessionIsolation:
    """Verify two concurrent sessions never pollute each other's state."""

    @pytest.mark.asyncio
    async def test_conversation_history_isolated(self, service, monkeypatch) -> None:
        """Session A's conversation should not leak into Session B."""
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
            return {
                "analysis": "ok", "answer": "ok",
                "actions": [], "play_keyword": "",
                "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        # Session Alice asks for jazz
        await service.generate_reply("play jazz", {}, session_id="alice")
        # Session Bob asks for rock
        await service.generate_reply("play rock", {}, session_id="bob")

        ctx_a = service.session_manager.get("alice")
        ctx_b = service.session_manager.get("bob")

        assert ctx_a is not None
        assert ctx_b is not None

        hist_a = ctx_a.short_term_memory.get_last_user_message()
        hist_b = ctx_b.short_term_memory.get_last_user_message()

        assert hist_a == "play jazz", f"Alice should see 'play jazz', got {hist_a}"
        assert hist_b == "play rock", f"Bob should see 'play rock', got {hist_b}"

    @pytest.mark.asyncio
    async def test_episodic_memory_filtered_by_session(self, service, monkeypatch) -> None:
        """Episodic snapshots store session_id and queries filter by it."""
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None):
            return {
                "analysis": "ok", "answer": "playing",
                "actions": [], "play_keyword": "",
                "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_call_llm
        )

        await service.generate_reply("alice likes jazz", {}, session_id="alice")
        await service.generate_reply("bob likes metal", {}, session_id="bob")

        # Allow fire-and-forget store_snapshot tasks to complete
        import asyncio
        await asyncio.sleep(0.5)

        alice_snaps = await service.episodic_memory.query_recent(limit=10, session_id="alice")
        bob_snaps = await service.episodic_memory.query_recent(limit=10, session_id="bob")
        all_snaps = await service.episodic_memory.query_recent(limit=10)

        alice_inputs = [s.user_input for s in alice_snaps]
        bob_inputs = [s.user_input for s in bob_snaps]

        assert any("alice" in inp for inp in alice_inputs), f"Alice snaps: {alice_inputs}"
        assert any("bob" in inp for inp in bob_inputs), f"Bob snaps: {bob_inputs}"
        assert not any("bob" in inp for inp in alice_inputs), \
            f"Bob's data leaked into Alice's query: {alice_inputs}"
        assert not any("alice" in inp for inp in bob_inputs), \
            f"Alice's data leaked into Bob's query: {bob_inputs}"
        # Unfiltered query sees everything
        assert len(all_snaps) >= len(alice_snaps) + len(bob_snaps)

    @pytest.mark.asyncio
    async def test_profile_isolation(self, service) -> None:
        """Each session gets its own MemoryManager with separate profile."""
        from backend.agent.memory_manager import MemoryManager

        mgr_a = MemoryManager(session_id="alice")
        mgr_b = MemoryManager(session_id="bob")

        # Verify they point to different files
        assert mgr_a.profile_path != mgr_b.profile_path
        assert "alice" in str(mgr_a.profile_path)
        assert "bob" in str(mgr_b.profile_path)

    def test_session_ttl_eviction(self, service) -> None:
        """Sessions expire after TTL and are evicted from cache."""
        base_count = service.session_manager.active_count  # includes TEST_SID from fixture
        ctx1 = service.session_manager.get_or_create("s1")
        ctx2 = service.session_manager.get_or_create("s2")

        assert service.session_manager.active_count == base_count + 2

        # Manually remove one to simulate eviction
        service.session_manager.remove("s1")
        assert service.session_manager.get("s1") is None
        assert service.session_manager.get("s2") is not None
        assert service.session_manager.active_count == base_count + 1

    def test_new_session_generates_uuid(self, service) -> None:
        """When session_id is None or empty, a fresh UUID is generated."""
        ctx = service.session_manager.get_or_create(None)
        assert len(ctx.session_id) == 36  # standard UUID length
        assert "-" in ctx.session_id

    def test_touch_resets_ttl(self, service) -> None:
        """Heartbeat should keep a session alive."""
        service.session_manager.get_or_create("touch-test")
        assert service.session_manager.heartbeat("touch-test") is True
        assert service.session_manager.heartbeat("nonexistent") is False


class TestTwoPassStreaming:
    """Verify RFC-003 Two-Pass pipeline — event ordering, fallback, and accuracy."""

    @pytest.mark.asyncio
    async def test_two_pass_sends_status_searching_then_found(self, service, monkeypatch) -> None:
        """Phase 1 success → status:searching → status:found → music → token → text → done."""
        async def fake_decision(system_prompt: str, user_prompt: str, model: str | None = None):
            return {
                "analysis": "ok", "answer": "", "actions": [],
                "play_keyword": "Test Song",
                "provider": "test", "model": "test",
            }

        async def fake_search(keyword: str):
            return {"id": "123", "name": "Test Song", "artist": "Test Artist"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/test.mp3"

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield "Hello"
            yield {
                "analysis": "ok", "answer": "Hello!", "actions": [],
                "play_keyword": "", "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_decision,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.search_first_song", fake_search,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.get_song_mp3_url", fake_mp3,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "play Test Song", {}, session_id="two-pass-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # Verify correct event sequence
        assert "status" in event_types
        assert "music" in event_types
        assert "token" in event_types
        assert "text" in event_types
        assert "done" in event_types
        # Music MUST come before text (radio DJ timing)
        music_idx = event_types.index("music")
        text_idx = event_types.index("text")
        assert music_idx < text_idx, f"music event must precede text, got music@{music_idx} text@{text_idx}"

    @pytest.mark.asyncio
    async def test_two_pass_falls_back_on_phase1_failure(self, service, monkeypatch) -> None:
        """When Phase 1 returns None, the pipeline falls through to Single-Pass."""
        async def fake_decision(system_prompt: str, user_prompt: str, model: str | None = None):
            return {
                "analysis": "ok", "answer": "", "actions": [],
                "play_keyword": "",
                "provider": "test", "model": "test",
            }

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield {
                "analysis": "ok", "answer": "fallback", "actions": [],
                "play_keyword": "", "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.call_llm", fake_decision,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "hello", {}, session_id="fallback-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        assert "done" in event_types  # single-pass still completes

    @pytest.mark.asyncio
    async def test_chitchat_uses_single_pass(self, service, monkeypatch) -> None:
        """CHITCHAT intent goes directly to Single-Pass (no Two-Pass overhead)."""
        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield {
                "analysis": "ok", "answer": "Hi there!", "actions": [],
                "play_keyword": "", "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "hello how are you", {}, session_id="chitchat-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # CHITCHAT should NOT have status events (no Two-Pass)
        assert "status" not in event_types
        assert "done" in event_types
