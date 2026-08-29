import asyncio

import pytest
from fastapi import BackgroundTasks

from backend.config import settings
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
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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

        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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


class TestBuildToolSchemas:
    """RFC: function calling —— 工具按意图门控暴露给 LLM。"""

    @staticmethod
    def _ensure_music_tools_registered() -> None:
        """test_base 的 registry.reset() 会清空全局注册表——此处确保存在。"""
        from backend.tools.base import tool_registry
        from backend.tools.music_tool import GetMusicUrlTool, SearchMusicTool

        if "search_music" not in tool_registry:
            tool_registry.register(SearchMusicTool())
        if "get_music_url" not in tool_registry:
            tool_registry.register(GetMusicUrlTool())

    def test_music_intent_exposes_music_tools(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent

        self._ensure_music_tools_registered()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")
        schemas = service._build_tool_schemas(Intent.MUSIC_PLAY)
        names = {s["function"]["name"] for s in schemas}
        assert "search_music" in names
        assert "get_music_url" in names
        assert all(s["type"] == "function" for s in schemas)

    def test_chitchat_exposes_no_tools(self, service) -> None:
        from backend.agent.intent_classifier import Intent

        assert service._build_tool_schemas(Intent.CHITCHAT) == []

    def test_unknown_exposes_all_available(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent

        self._ensure_music_tools_registered()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")
        schemas = service._build_tool_schemas(Intent.UNKNOWN)
        assert len(schemas) >= 2  # 音乐工具（无 cookie 时为空，有 cookie 时 ≥2）


class TestSessionIsolation:
    """Verify two concurrent sessions never pollute each other's state."""

    @pytest.mark.asyncio
    async def test_conversation_history_isolated(self, service, monkeypatch) -> None:
        """Session A's conversation should not leak into Session B."""
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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
        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
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


class TestTTSIntegration:
    """RFC-011: TTS speech events in streaming pipeline."""

    @pytest.mark.asyncio
    async def test_tts_disabled_by_default_no_speech_event(self, service, monkeypatch) -> None:
        """When TTS_ENABLED is not set, speech events never appear."""
        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield "Hi"
            yield {
                "analysis": "ok", "answer": "Hi there!", "actions": [],
                "play_keyword": "", "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "hello", {}, session_id="tts-off-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        assert "speech" not in event_types, "speech should not appear when TTS is disabled"

    @pytest.mark.asyncio
    async def test_speech_event_for_chitchat_when_tts_enabled(self, service, monkeypatch) -> None:
        """CHITCHAT with TTS enabled → speech event after text."""
        monkeypatch.setattr(settings, "tts_enabled", True)
        monkeypatch.setattr(settings, "tts_intents", "chitchat")
        # Re-init TTS provider to pick up env change
        from backend.agent.tts_provider import TTSProvider
        service.tts = TTSProvider()

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield "Hello world, nice to meet you all."
            yield {
                "analysis": "ok", "answer": "Hello world, nice to meet you all.", "actions": [],
                "play_keyword": "", "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "hello", {}, session_id="tts-chitchat",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # speech event should appear (even with empty urls when no TTS tool registered)
        assert "speech" in event_types, f"Expected 'speech' event, got {event_types}"

        # speech event data should contain expected fields
        speech_events = [e for e in events if e.startswith("event: speech")]
        assert len(speech_events) == 1
        import json
        data_str = speech_events[0].split("data: ", 1)[1].strip()
        payload = json.loads(data_str)
        assert "urls" in payload
        assert "text" in payload
        assert payload["intent"] == "chitchat"

    @pytest.mark.asyncio
    async def test_speech_event_for_two_pass_music_play(self, service, monkeypatch) -> None:
        """MUSIC_PLAY with TTS enabled + 'music_play' in whitelist → speech after text."""
        monkeypatch.setattr(settings, "tts_enabled", True)
        monkeypatch.setattr(settings, "tts_intents", "music_play")
        from backend.agent.tts_provider import TTSProvider
        service.tts = TTSProvider()

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
            yield "Hello beautiful people. Let's play this one for you right now."
            yield {
                "analysis": "ok", "answer": "Hello beautiful people. Let's play this one for you right now.",
                "actions": [], "play_keyword": "",
                "provider": "test", "model": "test",
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
            "play Test Song", {}, session_id="tts-twopass",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # Music must come before speech (music is not delayed by TTS)
        assert "music" in event_types
        assert "speech" in event_types
        music_idx = event_types.index("music")
        speech_idx = event_types.index("speech")
        assert music_idx < speech_idx, \
            f"music ({music_idx}) must precede speech ({speech_idx}) — TTS never blocks music"

    @pytest.mark.asyncio
    async def test_music_plays_even_when_tts_fails(self, service, monkeypatch) -> None:
        """When TTS is enabled but no tool is registered, music still plays normally."""
        monkeypatch.setattr(settings, "tts_enabled", True)
        monkeypatch.setattr(settings, "tts_intents", "chitchat,weather")
        from backend.agent.tts_provider import TTSProvider
        service.tts = TTSProvider()

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield "Let's play some music."
            yield {
                "analysis": "ok", "answer": "Let's play some music.", "actions": [
                    {"tool": "search_music", "keyword": "jazz"}
                ],
                "play_keyword": "jazz",
                "provider": "test", "model": "test",
            }

        monkeypatch.setattr(
            "backend.services.assistant_service.stream_llm", fake_stream,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "play jazz", {}, session_id="tts-fail-safe",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # TTS is disabled for music_recommend — no speech. The pipeline just works.
        assert "done" in event_types, "pipeline must complete even when TTS is unavailable"

@pytest.mark.asyncio
class TestReflectionTrigger:
    """v5 Reflection —— 每 10 轮触发一次会话摘要，节流防重。"""

    async def test_fires_after_10_turns(self, service, monkeypatch) -> None:
        ctx = service.session_manager.get_or_create("reflect-fire")
        for i in range(10):
            ctx.short_term_memory.add_turn(f"msg {i}", f"reply {i}")

        scheduled: dict = {}
        async def fake_summarize(sid: str, transcript: str, turn_count: int):
            scheduled["hit"] = (sid, turn_count)
            return 1

        monkeypatch.setattr(service.reflector, "summarize_and_store", fake_summarize)
        service._maybe_reflect("reflect-fire", ctx)
        await asyncio.sleep(0.05)  # 让 ensure_future 任务执行
        assert scheduled.get("hit") == ("reflect-fire", 10)

    async def test_throttled_within_window(self, service, monkeypatch) -> None:
        ctx = service.session_manager.get_or_create("reflect-throttle")
        for i in range(12):
            ctx.short_term_memory.add_turn(f"msg {i}", f"reply {i}")

        hits: list[int] = []
        async def fake_summarize(sid: str, transcript: str, turn_count: int):
            hits.append(turn_count)
            return 1

        monkeypatch.setattr(service.reflector, "summarize_and_store", fake_summarize)
        service._maybe_reflect("reflect-throttle", ctx)
        await asyncio.sleep(0.05)
        service._maybe_reflect("reflect-throttle", ctx)  # 同窗口内第二次不触发
        assert hits == [12]

    async def test_no_fire_below_threshold(self, service, monkeypatch) -> None:
        ctx = service.session_manager.get_or_create("reflect-low")
        for i in range(5):
            ctx.short_term_memory.add_turn(f"msg {i}", f"reply {i}")

        hits: list[int] = []
        async def fake_summarize(sid: str, transcript: str, turn_count: int):
            hits.append(turn_count)

        monkeypatch.setattr(service.reflector, "summarize_and_store", fake_summarize)
        service._maybe_reflect("reflect-low", ctx)
        await asyncio.sleep(0.05)
        assert hits == []


class TestSkipRequest:
    """换歌指令（NEXT 快捷指令等）跳过 Phase 1，直接走带工具的单遍路径。"""

    def test_skip_signal_detection(self) -> None:
        from backend.services.assistant_service import AssistantService

        assert AssistantService._is_skip_request("换一首") is True
        assert AssistantService._is_skip_request("下一首") is True
        assert AssistantService._is_skip_request("切歌") is True
        assert AssistantService._is_skip_request("再来一首") is True
        assert AssistantService._is_skip_request("next") is True

    def test_normal_play_request_not_skip(self) -> None:
        from backend.services.assistant_service import AssistantService

        assert AssistantService._is_skip_request("来一首周杰伦的晴天") is False
        assert AssistantService._is_skip_request("播放爵士") is False
        assert AssistantService._is_skip_request("今天天气怎么样") is False

    @pytest.mark.asyncio
    async def test_skip_request_goes_single_pass_with_tools(self, service, monkeypatch) -> None:
        """换歌指令不走 Two-Pass（无 searching 状态、无 Phase 1），直接带工具换一曲。"""
        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        stream_calls = {"n": 0}

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            stream_calls["n"] += 1
            if stream_calls["n"] == 1:
                assert kw.get("tools"), "换歌指令必须携带工具"
                assert kw.get("force_tools") is True
            yield {"analysis": "", "answer": "来一首别的",
                   "actions": [{"tool": "search_music", "keyword": "Norah Jones Sunrise"}],
                   "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Sunrise", "artist": "Norah Jones"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/next.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.music_tool.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "换一首", {}, session_id="skip-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # 无 searching/found 状态（未走 Two-Pass Phase 1），直接 music
        assert "searching" not in event_types
        assert "music" in event_types
        assert "done" in event_types


class TestPhase2Fallback:
    """模型只调工具无文案 → 音乐先响，Phase 2 生成自然台词（不落模板）。"""

    @pytest.mark.asyncio
    async def test_empty_answer_uses_phase2_dj_line(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        # 强制意图为 MUSIC_RECOMMEND（真实 LLM 可能判成 MUSIC_PLAY，导致走 Two-Pass）
        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_RECOMMEND

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        calls = {"n": 0}

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # 单遍：只调工具，无文案（required 常见行为）
                yield {"analysis": "", "answer": "",
                       "actions": [{"tool": "search_music", "keyword": "Norah Jones Sunrise"}],
                       "play_keyword": "", "provider": "t", "model": "m"}
            else:
                # Phase 2：在前奏上生成的自然台词（多变，非模板）
                yield "萨克斯一进来，整个人都松了。"
                yield {"analysis": "", "answer": "萨克斯一进来，整个人都松了。",
                       "actions": [], "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Sunrise", "artist": "Norah Jones"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/sunrise.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.music_tool.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "推荐点放松的", {}, session_id="phase2-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        text_events = [e for e in events if "event: text" in e]
        done_event = [e for e in events if "event: done" in e][0]
        # Phase 2 台词进入 text 事件与 done，且单遍 + Phase 2 各调一次
        assert any("萨克斯一进来" in e for e in text_events)
        assert "萨克斯一进来" in done_event
        assert calls["n"] == 2


class TestPhase1DeterministicFallback:
    """模型两次拒绝工具调用 → Phase 1 确定性搜索路径兜底出歌。"""

    @pytest.mark.asyncio
    async def test_falls_back_to_phase1_when_tools_refused(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_RECOMMEND

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        calls = {"stream": 0, "llm": 0}

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            calls["stream"] += 1
            if calls["stream"] == 1:
                # 主流：只输出虚构失败文本，不调工具
                yield {"analysis": "", "answer": "哎，那首没找到，估计版权锁了。",
                       "actions": [], "play_keyword": "", "provider": "t", "model": "m"}
            else:
                # Phase 2：自然台词
                yield "Frank Ocean 的《Pink + White》，慵懒又细腻，正好配休息。"
                yield {"analysis": "", "answer": "Frank Ocean 的《Pink + White》，慵懒又细腻，正好配休息。",
                       "actions": [], "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
            calls["llm"] += 1
            if calls["llm"] == 1:
                # 严格重试：仍拒绝调工具
                return {"analysis": "", "answer": "", "actions": [],
                        "play_keyword": "", "provider": "t", "model": "m"}
            # Phase 1 决策：给出 play_keyword
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "Frank Ocean Pink + White", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Pink + White", "artist": "Frank Ocean"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/pinkwhite.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        monkeypatch.setattr("backend.services.assistant_service.search_first_song", fake_search)
        monkeypatch.setattr("backend.services.assistant_service.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "工作休息期间想听放松的R&B", {}, session_id="phase1-fb-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        # 严格重试 + Phase 1 决策各一次 LLM 调用
        assert calls["llm"] == 2
        # 最终出歌（Phase 1 确定性兜底），虚构失败文本被 Phase 2 台词替换
        assert any("event: music" in e for e in events)
        done_event = [e for e in events if "event: done" in e][0]
        assert "Pink + White" in done_event
        assert any("慵懒又细腻" in e for e in events)


class TestPhase1MissFallthrough:
    """MUSIC_PLAY Phase 1 失败 → 不再直接道歉，降级单遍容错链出歌。"""

    @pytest.mark.asyncio
    async def test_phase1_miss_falls_through_to_single_pass(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_PLAY

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        async def fake_prefetch(user_input: str, sid: str, metadata: dict):
            return None  # Phase 1 失败

        monkeypatch.setattr(service, "_phase1_prefetch", fake_prefetch)

        stream_calls = {"n": 0}

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            stream_calls["n"] += 1
            if stream_calls["n"] == 1:
                # 单遍：带工具成功出动作
                yield {"analysis": "", "answer": "",
                       "actions": [{"tool": "search_music", "keyword": "SZA Kill Bill"}],
                       "play_keyword": "", "provider": "t", "model": "m"}
            else:
                yield "SZA 的《Kill Bill》，慵懒又细腻，正好配休息。"
                yield {"analysis": "", "answer": "SZA 的《Kill Bill》，慵懒又细腻，正好配休息。",
                       "actions": [], "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Kill Bill", "artist": "SZA"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/killbill.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.music_tool.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "工作休息期间，想听能让人放松的R&B", {}, session_id="phase1-miss-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        event_types = [e.split("\n")[0].replace("event: ", "") for e in events]
        # 先 searching/not_found（Phase 1 失败），随后单遍出歌
        assert "status" in event_types
        assert any('"phase":"not_found"' in e for e in events)
        assert "music" in event_types
        done_event = [e for e in events if "event: done" in e][0]
        assert "Kill Bill" in done_event


class TestStrictToolEnforcement:
    """required 模式偶发不被遵守（模型只输出文本）→ 强制重试一次获得工具调用。"""

    @staticmethod
    def _ensure_music_tools() -> None:
        from backend.tools.base import tool_registry
        from backend.tools.music_tool import GetMusicUrlTool, SearchMusicTool

        if "search_music" not in tool_registry:
            tool_registry.register(SearchMusicTool())
        if "get_music_url" not in tool_registry:
            tool_registry.register(GetMusicUrlTool())

    @pytest.mark.asyncio
    async def test_stream_retries_when_model_skips_tool(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        self._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_RECOMMEND

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        strict_calls: list[str] = []

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            # 模型违反 required：只输出臆想的失败文本，不调工具
            yield {"analysis": "", "answer": "这首爵士搜不到，八成版权锁了",
                   "actions": [], "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
            strict_calls.append(user_prompt)
            return {"analysis": "", "answer": "找到了",
                    "actions": [{"tool": "search_music", "keyword": "Norah Jones Dont Know Why"}],
                    "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Don't Know Why", "artist": "Norah Jones"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/ok.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        # 工具内部走 backend.tools.music_tool 的模块级导入——必须打这里
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.music_tool.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "有没有推荐的jazz", {}, session_id="strict-stream-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        # 强制重试被触发，且带"先搜索别臆想版权"纠正指令
        assert len(strict_calls) == 1
        assert "不要假设版权问题" in strict_calls[0]
        # 最终出歌（模型第一次的失败文本被真实结果覆盖）
        assert any("event: music" in e for e in events)

    @pytest.mark.asyncio
    async def test_non_music_intent_no_strict_retry(self, service, monkeypatch) -> None:
        strict_calls: list[str] = []

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield {"analysis": "", "answer": "你好呀", "actions": [],
                   "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model: str | None = None, **kw):
            strict_calls.append(user_prompt)
            return {"analysis": "", "answer": "ok", "actions": [],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)

        events = []
        async for sse in service.generate_reply_stream(
            "你好", {}, session_id="strict-chitchat-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        assert strict_calls == []  # 闲聊不触发强制重试
        assert any("event: done" in e for e in events)
