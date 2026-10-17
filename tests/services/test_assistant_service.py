import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

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

    def get_artist_constraints(self) -> dict:
        return {"liked": ["Artist A"], "disliked": list(getattr(self, "disliked", []))}

    async def async_update_profile(self, user_input: str, assistant_reply: str, summaries=None):
        return {"ok": True, "user_input": user_input, "assistant_reply": assistant_reply}


@pytest.fixture
def service(tmp_path, monkeypatch):
    # 重定向运行时数据目录（trace/profiles 不污染真实 backend/data）
    monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
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
        # T-N23-5：死字段已删除 —— 断言「不含该键」而非「等于空串」，
        # 后者在字段被硬编码为 "" 时恒真，是假绿。
        assert "play_keyword" not in reply
        assert "music" not in reply


class RecordingMemoryManager(StubMemoryManager):
    """记录 async_update_profile 的入参，用于断言 summaries 真正抵达 Observer。

    N22 的教训：只断言「注册了哪个函数」的测试毫无价值 —— 摘要恒为空也能绿。
    这里改为记录真实入参，让功能是否生效变成可断言的事实。
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.update_calls: list[dict[str, Any]] = []
        self.error = error

    async def async_update_profile(
        self, user_input: str, assistant_reply: str, summaries=None,
    ) -> dict[str, Any]:
        self.update_calls.append({
            "user_input": user_input,
            "assistant_reply": assistant_reply,
            "summaries": summaries,
        })
        if self.error is not None:
            raise self.error
        return {"ok": True}


def _install_recording_manager(
    service: AssistantService, error: Exception | None = None,
) -> RecordingMemoryManager:
    """把记录型 MemoryManager 注入装配好的 session context。"""
    recorder = RecordingMemoryManager(error=error)
    service.session_manager.get_or_create(TEST_SID).memory_manager = recorder
    return recorder


def _trace_records(tmp_path) -> list[dict[str, Any]]:
    """读取本次测试写入的会话 trace 记录（conversations.jsonl）。"""
    log = tmp_path / "conversations.jsonl"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestScheduleProfileUpdate:
    """后台画像更新调度 —— N22：摘要必须在协程里 await 后才会流到 Observer。"""

    def test_registers_background_task(self, service) -> None:
        bg = BackgroundTasks()
        service.schedule_profile_update(bg, "hello", {"answer": "ok"}, session_id=TEST_SID)
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func.__name__ == "_update_profile_with_summaries"

    @pytest.mark.asyncio
    async def test_summaries_reach_observer_payload(
        self, service, monkeypatch
    ) -> None:
        """T-N22-1：跨会话整合真的生效 —— summaries 非空且按序到达。

        这条是主验收之一：回退成同步 for 迭代时 summaries 恒为 []，必然红。
        """
        rows = [
            {"summary_text": "摘要A"},
            {"summary_text": "  摘要B  "},
            {"summary_text": "   "},
            {"summary_text": None},
        ]
        monkeypatch.setattr(
            service.episodic_memory,
            "query_recent_summaries",
            AsyncMock(return_value=rows),
        )
        recorder = _install_recording_manager(service)

        bg = BackgroundTasks()
        service.schedule_profile_update(
            bg, "我最近在听爵士", {"answer": "好的"}, session_id=TEST_SID,
        )
        await bg()  # Starlette 的 BackgroundTasks 自身可 await —— 真跑后台任务

        assert len(recorder.update_calls) == 1
        assert recorder.update_calls[0]["user_input"] == "我最近在听爵士"
        assert json.loads(recorder.update_calls[0]["assistant_reply"]) == {"answer": "好的"}
        assert recorder.update_calls[0]["summaries"] == ["摘要A", "摘要B"]

    @pytest.mark.asyncio
    async def test_summary_read_failure_degrades_but_stays_visible(
        self, service, monkeypatch, caplog, tmp_path
    ) -> None:
        """T-N22-2：摘要读取失败 → 画像仍更新（收到 []），且失败可见。"""
        monkeypatch.setattr(
            service.episodic_memory,
            "query_recent_summaries",
            AsyncMock(side_effect=RuntimeError("database is locked")),
        )
        recorder = _install_recording_manager(service)

        bg = BackgroundTasks()
        with caplog.at_level(logging.WARNING):
            service.schedule_profile_update(
                bg, "hi", {"answer": "ok"}, session_id=TEST_SID,
            )
            await bg()

        assert len(recorder.update_calls) == 1
        assert recorder.update_calls[0]["summaries"] == []
        warnings_text = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("最近摘要读取失败" in m for m in warnings_text)
        assert "bg:profile_summaries" in [r["path"] for r in _trace_records(tmp_path)]

    @pytest.mark.asyncio
    async def test_profile_update_failure_is_visible(
        self, service, monkeypatch, caplog, tmp_path
    ) -> None:
        """T-N22-3：画像更新本身抛异常 → 有 WARNING + trace，且不向 ASGI 层抛。"""
        monkeypatch.setattr(
            service.episodic_memory,
            "query_recent_summaries",
            AsyncMock(return_value=[{"summary_text": "摘要A"}]),
        )
        recorder = _install_recording_manager(
            service, error=ValueError("observer model down"),
        )

        bg = BackgroundTasks()
        with caplog.at_level(logging.WARNING):
            service.schedule_profile_update(
                bg, "hi", {"answer": "ok"}, session_id=TEST_SID,
            )
            await bg()  # 后台任务异常不得逃逸到响应链路

        assert len(recorder.update_calls) == 1
        warnings_text = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("画像更新失败" in m for m in warnings_text)
        assert "bg:profile_update" in [r["path"] for r in _trace_records(tmp_path)]

    @pytest.mark.asyncio
    async def test_summary_query_awaited_exactly_once(
        self, service, monkeypatch
    ) -> None:
        """T-N22-4：确定性判据 —— 摘要查询被 await 恰好一次（不依赖 GC 时机）。

        调度阶段不许 await（薄调度，不阻塞响应），后台任务执行时才 await。
        回退成 ``for item in self.episodic_memory.query_recent_summaries(...)``
        时 await_count 恒为 0，此断言必然红 —— 不依赖 RuntimeWarning 的 GC 时机。
        """
        query_mock = AsyncMock(return_value=[{"summary_text": "摘要A"}])
        monkeypatch.setattr(
            service.episodic_memory, "query_recent_summaries", query_mock,
        )
        _install_recording_manager(service)

        bg = BackgroundTasks()
        service.schedule_profile_update(bg, "hi", {"answer": "ok"}, session_id=TEST_SID)
        assert query_mock.await_count == 0

        await bg()

        assert query_mock.await_count == 1
        assert query_mock.await_args.args[0] == TEST_SID
        assert query_mock.await_args.kwargs["limit"] == 2

    @pytest.mark.asyncio
    async def test_missing_memory_manager_does_not_crash(
        self, service, monkeypatch, caplog, tmp_path
    ) -> None:
        """守卫：session context 未注入 MemoryManager 时后台任务不得炸掉。"""
        monkeypatch.setattr(
            service.episodic_memory,
            "query_recent_summaries",
            AsyncMock(return_value=[]),
        )
        service.session_manager.get_or_create(TEST_SID).memory_manager = None

        bg = BackgroundTasks()
        with caplog.at_level(logging.WARNING):
            service.schedule_profile_update(
                bg, "hi", {"answer": "ok"}, session_id=TEST_SID,
            )
            await bg()

        warnings_text = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("memory_manager 未初始化" in m for m in warnings_text)
        assert "bg:profile_update" in [r["path"] for r in _trace_records(tmp_path)]


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
        service.session_manager.get_or_create("s1")
        service.session_manager.get_or_create("s2")

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
        async def fake_decision(messages, model=None, temperature=0.1):
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
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
            "backend.services.assistant_service.request_json_object", fake_decision,
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
        async def fake_decision(messages, model=None, temperature=0.1):
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
            "backend.services.assistant_service.request_json_object", fake_decision,
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


class TestPhase1JsonContract:
    """Phase 1 契约测试（RFC-019 / N1）：mock 到 HTTP 传输层，验证 JSON 链路真能取词。

    刻意 **不** mock 函数返回值 —— 只替换 httpx 传输层，因此断言的是
    「请求构造 → JSON 解析 → play_keyword 提取」整条链路，
    而不是「mock 替我填好了 play_keyword」。
    """

    SONG = {
        "id": "186016",
        "name": "晴天",
        "artist": "周杰伦",
        "cover_url": "http://example.com/qingtian.jpg",
        "album": "叶惠美",
        "duration_ms": 269000,
    }

    @staticmethod
    def _install_chat_transport(monkeypatch, content: str) -> dict:
        """把 httpx.AsyncClient 换成 MockTransport，返回可断言的请求捕获槽。"""
        import json as _json

        import httpx

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = _json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-contract",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return captured

    @pytest.mark.asyncio
    async def test_phase1_prefetch_extracts_keyword_from_json_response(
        self, service, monkeypatch,
    ) -> None:
        """HTTP 返回 play_keyword → _phase1_prefetch 必须返回非 None（N1 回归防线）。"""
        monkeypatch.setattr(settings, "llm_api_key", "test-key")
        captured = self._install_chat_transport(
            monkeypatch,
            '{"analysis":"用户想听晴天","answer":"","actions":[],'
            '"play_keyword":"周杰伦 晴天"}',
        )

        search_calls: list[str] = []

        async def fake_search(keyword: str):
            search_calls.append(keyword)
            return dict(self.SONG)

        async def fake_mp3(song_id: str, level: str = "standard"):
            return f"http://example.com/{song_id}.mp3"

        monkeypatch.setattr(
            "backend.services.assistant_service.search_first_song", fake_search,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.get_song_mp3_url", fake_mp3,
        )

        song = await service._phase1_prefetch("来首晴天", TEST_SID, {})

        assert song is not None, (
            "Phase 1 必须能从 JSON 响应里取出 play_keyword；返回 None 说明"
            " 走的是自然语言分支（硬编码 play_keyword=''，N1）"
        )
        assert song["requested_keyword"] == "周杰伦 晴天"
        assert song["song_id"] == "186016"
        assert song["name"] == "晴天"
        assert song["artist"] == "周杰伦"
        assert song["mp3_url"] == "http://example.com/186016.mp3"
        assert search_calls == ["周杰伦 晴天"]

        # 请求体必须是 JSON 模式 —— 自然语言分支（_request_chat_plain）没有该字段
        body = captured["body"]
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"].endswith("User: 来首晴天")
        assert "/chat/completions" in captured["url"]

    @pytest.mark.asyncio
    async def test_phase1_prefetch_returns_none_without_keyword(
        self, service, monkeypatch,
    ) -> None:
        """HTTP 返回无 play_keyword 的 JSON → _phase1_prefetch 返回 None 且不搜索。"""
        monkeypatch.setattr(settings, "llm_api_key", "test-key")
        self._install_chat_transport(
            monkeypatch, '{"analysis":"不是点歌","answer":"","actions":[]}',
        )

        search_calls: list[str] = []

        async def fake_search(keyword: str):
            search_calls.append(keyword)
            return dict(self.SONG)

        monkeypatch.setattr(
            "backend.services.assistant_service.search_first_song", fake_search,
        )

        song = await service._phase1_prefetch("今天天气怎么样", TEST_SID, {})

        assert song is None
        assert search_calls == []


class TestPlayKeywordRemoval:
    """RFC-019 / N23 —— `play_keyword` 死字段已从 Reply 契约中删除。

    保留的两处（Prompt 契约层 `prompts.py`、Phase-1 解析层 `_phase1_prefetch`）
    不在本类覆盖范围内；本类只锁定「Reply 层绝不再吐出该键」，其中
    T-N23-6 是 N1 的保护测试，防止本次清理误伤 Phase-1。
    """

    @pytest.fixture
    def llm_env(self, tmp_path, monkeypatch):
        """重定向 LLM 客户端的数据目录并固定 base_url/model，避免外网与真实落盘。"""
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
        monkeypatch.setattr(settings, "llm_model", "test-model")
        monkeypatch.setattr(settings, "llm_disable_thinking", False)
        return settings

    # ------------------------------------------------------------------
    # T-N23-1 —— _normalize_response 不再生成该键
    # ------------------------------------------------------------------
    def test_normalize_response_omits_play_keyword(self) -> None:
        from backend.agent.llm_client import _normalize_response

        # 输入里即使带着该键，也必须被拒之门外（否则等于「换个名字继续传播」）
        out = _normalize_response(
            {
                "analysis": "a",
                "answer": "b",
                "actions": [{"tool": "search_music", "keyword": "x"}],
                "play_keyword": "Artist SongTitle",
            },
            "test-provider",
            "test-model",
        )

        assert "play_keyword" not in out
        # 其余契约字段必须完好无损
        assert out["analysis"] == "a"
        assert out["answer"] == "b"
        assert out["actions"] == [{"tool": "search_music", "keyword": "x"}]
        assert out["provider"] == "test-provider"
        assert out["model"] == "test-model"

    # ------------------------------------------------------------------
    # T-N23-2 —— call_llm 四个分支均不含该键
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_call_llm_no_api_key_branch_omits_play_keyword(
        self, llm_env, monkeypatch,
    ) -> None:
        from backend.agent.llm_client import call_llm

        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "openai_api_key", "")
        monkeypatch.setattr(settings, "deepseek_api_key", "")

        reply = await call_llm("sys", "user")

        assert "play_keyword" not in reply
        assert reply["analysis"] == "Missing API key."

    @pytest.mark.asyncio
    async def test_call_llm_tools_branch_omits_play_keyword(
        self, llm_env, monkeypatch,
    ) -> None:
        from backend.agent import llm_client
        from backend.agent.llm_client import call_llm

        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        async def fake_with_tools(config, messages, temperature, tools, **kwargs):
            return "好的", [
                {"function": {"name": "search_music", "arguments": '{"keyword": "x"}'}},
            ]

        monkeypatch.setattr(llm_client, "_request_chat_with_tools", fake_with_tools)

        reply = await call_llm("sys", "user", tools=[{"type": "function"}])

        assert "play_keyword" not in reply
        assert reply["actions"] == [{"tool": "search_music", "keyword": "x"}]

    @pytest.mark.asyncio
    async def test_call_llm_plain_branch_omits_play_keyword(
        self, llm_env, monkeypatch,
    ) -> None:
        from backend.agent import llm_client
        from backend.agent.llm_client import call_llm

        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        async def fake_plain(config, messages, temperature, timeout=30.0):
            return "纯文本回答"

        monkeypatch.setattr(llm_client, "_request_chat_plain", fake_plain)

        reply = await call_llm("sys", "user")

        assert "play_keyword" not in reply
        assert reply["answer"] == "纯文本回答"
        assert reply["actions"] == []

    @pytest.mark.asyncio
    async def test_call_llm_exception_branch_omits_play_keyword(
        self, llm_env, monkeypatch,
    ) -> None:
        import httpx

        from backend.agent import llm_client
        from backend.agent.llm_client import call_llm

        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        async def boom(config, messages, temperature, timeout=30.0):
            raise httpx.ConnectError("network down")

        monkeypatch.setattr(llm_client, "_request_chat_plain", boom)

        reply = await call_llm("sys", "user")

        assert "play_keyword" not in reply
        assert reply["analysis"] == "Model call failed."

    # ------------------------------------------------------------------
    # T-N23-3 —— stream_llm 最终 dict 不含该键（正常流 + 连接失败两条路径）
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_stream_llm_omits_play_keyword_on_success(
        self, llm_env, monkeypatch,
    ) -> None:
        import httpx

        from backend.agent.llm_client import stream_llm

        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        # 不塞 [DONE] 哨兵：让 aiter_lines 自然耗尽，避免提前 break 留下
        # 未关闭的异步生成器（会污染测试输出的 RuntimeWarning）
        sse = (
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            'data: {"choices":[{"delta":{}}]}\n\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=sse,
                headers={"content-type": "text/event-stream"},
            )

        real_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

        chunks: list = []
        async for item in stream_llm("sys", "user"):
            chunks.append(item)

        tokens = [c for c in chunks if isinstance(c, str)]
        finals = [c for c in chunks if isinstance(c, dict)]

        assert tokens == ["Hello"]
        assert len(finals) == 1
        assert "play_keyword" not in finals[0]
        assert finals[0]["answer"] == "Hello"
        assert finals[0]["stream_interrupted"] is False

    @pytest.mark.asyncio
    async def test_stream_llm_omits_play_keyword_on_connection_error(
        self, llm_env, monkeypatch,
    ) -> None:
        import httpx

        from backend.agent.llm_client import stream_llm

        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> bool:
                return False

            def stream(self, *args, **kwargs):
                raise httpx.ConnectError("cannot connect")

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        finals: list = []
        async for item in stream_llm("sys", "user", max_retries=0):
            if isinstance(item, dict):
                finals.append(item)

        assert len(finals) == 1
        assert "play_keyword" not in finals[0]
        assert finals[0]["retries_exhausted"] is True

    # ------------------------------------------------------------------
    # T-N23-4 —— 降级兜底对象不再注入该键，且剥离残留
    # ------------------------------------------------------------------
    def test_build_graceful_fallback_omits_play_keyword(self) -> None:
        reply = {
            "analysis": "ok",
            "answer": "playing",
            "actions": [
                {"tool": "search_music", "keyword": "x"},
                {"tool": "get_music_url", "song_id": "1"},
            ],
            "music": {"song_id": "1"},
            "play_keyword": "Artist SongTitle",  # 模拟 mock / 历史残留
            "provider": "test",
            "model": "test",
        }

        degraded = AssistantService._build_graceful_fallback(reply)

        assert "play_keyword" not in degraded
        assert "music" not in degraded
        assert degraded["actions"] == []  # 音乐工具被清空
        assert degraded["answer"] == degraded["say"]
        # 入参不被就地修改
        assert reply["play_keyword"] == "Artist SongTitle"

    # ------------------------------------------------------------------
    # T-N23-6 🔒 —— 保护 N1：Reply 层删字段不得影响 Phase-1 的 JSON 取值
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_phase1_prefetch_still_extracts_keyword_after_removal(
        self, service, monkeypatch,
    ) -> None:
        """HTTP 返回含 play_keyword 的 JSON → _phase1_prefetch 仍返回非 None。

        这条测试跨过 Reply 层：Phase-1 走 request_json_object →
        _request_chat_json → _extract_json，与 _normalize_response 无关。
        若有人把 `_phase1_prefetch` 改回 call_llm，本测试立刻变红。
        """
        monkeypatch.setattr(settings, "llm_api_key", "test-key")
        captured = TestPhase1JsonContract._install_chat_transport(
            monkeypatch,
            '{"analysis":"点歌","answer":"","actions":[],'
            '"play_keyword":"陈奕迅 富士山下"}',
        )

        searched: list[str] = []

        async def fake_search(keyword: str):
            searched.append(keyword)
            return {
                "id": "999", "name": "富士山下", "artist": "陈奕迅",
                "cover_url": "", "album": "", "duration_ms": 1,
            }

        async def fake_mp3(song_id: str, level: str = "standard"):
            return f"http://example.com/{song_id}.mp3"

        monkeypatch.setattr(
            "backend.services.assistant_service.search_first_song", fake_search,
        )
        monkeypatch.setattr(
            "backend.services.assistant_service.get_song_mp3_url", fake_mp3,
        )

        song = await service._phase1_prefetch("来首富士山下", TEST_SID, {})

        assert song is not None, (
            "删除 play_keyword 后 Phase-1 取词链路必须完好（N1 回归）：返回 None "
            "说明取值改走了 call_llm（其非工具分支只有自然语言，没有关键词）"
        )
        assert song["requested_keyword"] == "陈奕迅 富士山下"
        assert song["song_id"] == "999"
        assert searched == ["陈奕迅 富士山下"]
        # 请求体仍是 JSON 模式 —— 与 Reply 层的 _normalize_response 无交集
        assert captured["body"]["response_format"] == {"type": "json_object"}


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

        async def fake_decision(messages, model=None, temperature=0.1):
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
            "backend.services.assistant_service.request_json_object", fake_decision,
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

        calls = {"stream": 0, "llm": 0, "json": 0}

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
            """严格重试：仍拒绝调工具。"""
            calls["llm"] += 1
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_json_decision(messages, model=None, temperature=0.1):
            """Phase 1 决策（JSON 路径）：给出 play_keyword。"""
            calls["json"] += 1
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "Frank Ocean Pink + White", "provider": "t", "model": "m"}

        async def fake_search(keyword: str):
            return {"id": "1", "name": "Pink + White", "artist": "Frank Ocean"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://example.com/pinkwhite.mp3"

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        monkeypatch.setattr(
            "backend.services.assistant_service.request_json_object", fake_json_decision,
        )
        monkeypatch.setattr("backend.services.assistant_service.search_first_song", fake_search)
        monkeypatch.setattr("backend.services.assistant_service.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "工作休息期间想听放松的R&B", {}, session_id="phase1-fb-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        # 严格重试（call_llm）+ Phase 1 决策（request_json_object）各一次
        assert calls["llm"] == 1
        assert calls["json"] == 1
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


class TestConversationTrace:
    """会话级 trace 日志 —— 交互记录写入 conversations.jsonl。"""
    def test_trace_writes_jsonl(self, monkeypatch, tmp_path) -> None:
        import json
        import time

        from backend.agent.intent_classifier import Intent
        from backend.services.assistant_service import AssistantService

        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        AssistantService._trace(
            "s1", "来首轻松的", Intent.MUSIC_RECOMMEND, "single_pass",
            time.monotonic() - 1.5,
            reply={
                "answer": "来点轻松的",
                "actions": [{"tool": "search_music", "keyword": "Miles Davis So What"}],
                "music": {"name": "So What", "artist": "Miles Davis", "song_id": "1"},
            },
        )
        log = tmp_path / "conversations.jsonl"
        assert log.exists()
        record = json.loads(log.read_text(encoding="utf-8").strip())
        assert record["session_id"] == "s1"
        assert record["intent"] == "music_recommend"
        assert record["path"] == "single_pass"
        assert record["tool_calls"] == [{"tool": "search_music", "keyword": "Miles Davis So What"}]
        assert record["music"] == {"name": "So What", "artist": "Miles Davis", "song_id": "1"}
        assert record["latency_ms"] >= 1000
        assert record["error"] is None

    def test_trace_error_record(self, monkeypatch, tmp_path) -> None:
        import json
        import time

        from backend.agent.intent_classifier import Intent
        from backend.services.assistant_service import AssistantService

        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        AssistantService._trace(
            "s2", "你好", Intent.CHITCHAT, "single_pass",
            time.monotonic(), error="Model call failed.",
        )
        record = json.loads((tmp_path / "conversations.jsonl").read_text(encoding="utf-8").strip())
        assert record["intent"] == "chitchat"
        assert record["error"] == "Model call failed."
        assert record["music"] is None
        assert record["answer"] == ""

    def test_trace_never_raises_on_bad_dir(self, monkeypatch) -> None:
        import time

        from backend.agent.intent_classifier import Intent
        from backend.services.assistant_service import AssistantService

        def boom():
            raise PermissionError("dir not writable")

        monkeypatch.setattr("backend.data_config.get_data_dir", boom)
        AssistantService._trace("s3", "x", Intent.UNKNOWN, "p", time.monotonic())  # 不应抛异常


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


class TestPreviousRequest:
    """上一首（v0.7 P1-2）—— 从播放历史回放，不经 LLM 选曲。"""

    def test_previous_signal_detection(self) -> None:
        from backend.services.assistant_service import AssistantService

        assert AssistantService._is_previous_request("上一首") is True
        assert AssistantService._is_previous_request("前一首") is True
        assert AssistantService._is_previous_request("previous") is True
        assert AssistantService._is_previous_request("换一首") is False
        assert AssistantService._is_previous_request("来一首周杰伦") is False

    @pytest.mark.asyncio
    async def test_replays_previous_from_history(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier
        from backend.memory.models import EpisodicSnapshot

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_PLAY

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        def snap(i, song_id, name, artist):
            return EpisodicSnapshot(
                id=i, timestamp="2026-09-05T00:00:00Z", user_input="x",
                assistant_reply="", played_song_name=name,
                played_song_artist=artist, mood_tag=None, weather_tag=None,
                time_of_day="night", genre_tag=None, song_id=song_id,
            )

        async def fake_history(session_id, limit=5):
            # 倒序：当前曲 id=2，上一首 id=1
            return [snap(2, "2", "B", "ArtistB"), snap(1, "1", "A", "ArtistA")]

        monkeypatch.setattr(service.episodic_memory, "query_recent_played", fake_history)

        async def fake_mp3(song_id, level="standard"):
            return f"http://x/{song_id}.mp3"

        monkeypatch.setattr(
            "backend.services.assistant_service.get_song_mp3_url", fake_mp3,
        )

        async def fake_stream(system_prompt, user_prompt, **kw):
            yield {"analysis": "", "answer": "回到上一首，ArtistA 的 A。",
                   "actions": [], "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)

        events = []
        async for sse in service.generate_reply_stream(
            "上一首", {"Currently Playing ID": "2"}, session_id="prev-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        music_events = [e for e in events if "event: music" in e]
        assert len(music_events) == 1
        assert '"song_id": "1"' in music_events[0]
        done = [e for e in events if "event: done" in e][0]
        assert "ArtistA" in done

    @pytest.mark.asyncio
    async def test_no_history_falls_through(self, service, monkeypatch) -> None:
        """无历史 → 走正常流程（单遍容错链），不报错。"""
        from backend.agent.intent_classifier import Intent, IntentClassifier

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_PLAY

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        async def fake_history(session_id, limit=5):
            return []

        monkeypatch.setattr(service.episodic_memory, "query_recent_played", fake_history)

        async def fake_stream(system_prompt, user_prompt, **kw):
            yield {"analysis": "", "answer": "好的", "actions": [],
                   "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt, user_prompt, model=None, **kw):
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_json_decision(messages, model=None, temperature=0.1):
            # Phase 1 未识别出关键词 → None → 继续走单遍
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        monkeypatch.setattr(
            "backend.services.assistant_service.request_json_object", fake_json_decision,
        )

        events = []
        async for sse in service.generate_reply_stream(
            "上一首", {}, session_id="prev-no-history",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        assert any("event: done" in e for e in events)


class TestDislikedEnforcement:
    """口味硬约束（v0.7 P0-2）—— disliked 命中 → 重试换艺人，仍命中则不播。"""

    @pytest.mark.asyncio
    async def test_disliked_retried_with_different_artist(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_RECOMMEND

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        # 会话画像：不喜欢的艺人
        ctx = service.session_manager.get_or_create("disliked-test")
        ctx.memory_manager = StubMemoryManager()
        ctx.memory_manager.disliked = ["RyaVocal"]

        search_calls = {"n": 0}

        async def fake_search(keyword: str):
            search_calls["n"] += 1
            if search_calls["n"] == 1:
                return {"id": "1", "name": "晴天", "artist": "RyaVocal"}
            return {"id": "2", "name": "爱爱爱", "artist": "方大同"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return f"http://x/{song_id}.mp3"

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            # 首搜命中 disliked 艺人
            yield {"analysis": "", "answer": "",
                   "actions": [{"tool": "search_music", "keyword": "周杰伦 晴天"}],
                   "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model=None, **kw):
            # disliked 重试：换艺人
            assert "RyaVocal" in user_prompt
            return {"analysis": "", "answer": "",
                    "actions": [{"tool": "search_music", "keyword": "方大同 爱爱爱"}],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.netease_api.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "推荐点歌", {}, session_id="disliked-test",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        music_events = [e for e in events if "event: music" in e]
        assert len(music_events) == 1
        assert "方大同" in music_events[0]
        assert "RyaVocal" not in music_events[0]

    @pytest.mark.asyncio
    async def test_disliked_twice_drops_music(self, service, monkeypatch) -> None:
        from backend.agent.intent_classifier import Intent, IntentClassifier

        TestStrictToolEnforcement._ensure_music_tools()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        async def fake_classify(self, user_input: str):
            return Intent.MUSIC_RECOMMEND

        monkeypatch.setattr(IntentClassifier, "classify_async", fake_classify)

        ctx = service.session_manager.get_or_create("disliked-twice")
        ctx.memory_manager = StubMemoryManager()
        ctx.memory_manager.disliked = ["RyaVocal"]

        async def fake_search(keyword: str):
            return {"id": "1", "name": "晴天", "artist": "RyaVocal"}

        async def fake_mp3(song_id: str, level: str = "standard"):
            return "http://x/1.mp3"

        async def fake_stream(system_prompt: str, user_prompt: str, **kw):
            yield {"analysis": "", "answer": "",
                   "actions": [{"tool": "search_music", "keyword": "周杰伦 晴天"}],
                   "play_keyword": "", "provider": "t", "model": "m"}

        async def fake_call_llm(system_prompt: str, user_prompt: str, model=None, **kw):
            # 重试仍推 disliked 艺人
            return {"analysis": "", "answer": "",
                    "actions": [{"tool": "search_music", "keyword": "周杰伦 晴天"}],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr("backend.services.assistant_service.stream_llm", fake_stream)
        monkeypatch.setattr("backend.services.assistant_service.call_llm", fake_call_llm)
        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)
        monkeypatch.setattr("backend.tools.netease_api.get_song_mp3_url", fake_mp3)

        events = []
        async for sse in service.generate_reply_stream(
            "推荐点歌", {}, session_id="disliked-twice",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        assert not any("event: music" in e for e in events)
        assert any("event: done" in e for e in events)


class TestAutoContinue:
    """自动续播（RFC-020 契约 D）—— 按品味选下一首，排除最近播放。"""

    def test_auto_continue_signal_detection(self) -> None:
        assert AssistantService._is_auto_continue({"Auto Continue": "true"}) is True
        assert AssistantService._is_auto_continue({"Auto Continue": "TRUE"}) is True
        assert AssistantService._is_auto_continue({"Auto Continue": "1"}) is False
        assert AssistantService._is_auto_continue({}) is False
        assert AssistantService._is_auto_continue(None) is False

    @pytest.mark.asyncio
    async def test_simi_pick_excludes_recent(self, service, monkeypatch) -> None:
        """相似优先：排除最近播放后选择第一个可播候选。"""
        from backend.services import assistant_service as svc_mod

        ctx = service.session_manager.get_or_create("auto-simi")
        ctx.memory_manager = StubMemoryManager()

        recent = [
            {"song_id": "cur", "name": "Current", "artist": "A",
             "cover_url": "", "album": "", "duration_ms": None,
             "requested_keyword": "", "played_at": "", "session_id": "auto-simi"},
        ]

        async def fake_list(session_id=None, limit=50):
            return recent

        monkeypatch.setattr(service.play_history, "list_async", fake_list)

        async def fake_simi(song_id, limit=10):
            assert song_id == "cur"
            return [
                {"id": "cur", "name": "Current", "artist": "A",
                 "cover_url": "", "album": "", "duration_ms": None},
                {"id": "next", "name": "Next Song", "artist": "B",
                 "cover_url": "http://c/1.jpg", "album": "Alb",
                 "duration_ms": 200000},
            ]

        monkeypatch.setattr(svc_mod, "find_similar_songs", fake_simi)

        async def fake_mp3(song_id, level="standard"):
            return f"http://x/{song_id}.mp3"

        monkeypatch.setattr(svc_mod, "get_song_mp3_url", fake_mp3)

        async def fake_stream(system_prompt, user_prompt, **kw):
            yield {"analysis": "", "answer": "电台继续。", "actions": [],
                   "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr(svc_mod, "stream_llm", fake_stream)

        events = []
        async for sse in service.generate_reply_stream(
            "", {"Auto Continue": "true"}, session_id="auto-simi",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        music_events = [e for e in events if "event: music" in e]
        assert len(music_events) == 1
        assert '"song_id": "next"' in music_events[0]
        assert '"cover_url": "http://c/1.jpg"' in music_events[0]
        assert any("event: done" in e for e in events)

    @pytest.mark.asyncio
    async def test_llm_fallback_picks_by_taste(self, service, monkeypatch) -> None:
        """相似为空 → LLM 带工具按画像挑歌。"""
        from backend.services import assistant_service as svc_mod

        TestBuildToolSchemas._ensure_music_tools_registered()
        monkeypatch.setattr(settings, "netease_cookie", "test-cookie")

        ctx = service.session_manager.get_or_create("auto-llm")
        ctx.memory_manager = StubMemoryManager()

        recent = [
            {"song_id": "cur", "name": "Current", "artist": "A",
             "cover_url": "", "album": "", "duration_ms": None,
             "requested_keyword": "", "played_at": "", "session_id": "auto-llm"},
        ]

        async def fake_list(session_id=None, limit=50):
            return recent

        monkeypatch.setattr(service.play_history, "list_async", fake_list)

        async def fake_simi(song_id, limit=10):
            return []

        monkeypatch.setattr(svc_mod, "find_similar_songs", fake_simi)

        async def fake_call_llm(system_prompt, user_prompt, model=None, **kw):
            assert "自动续播" in user_prompt
            return {"analysis": "", "answer": "",
                    "actions": [{"tool": "search_music", "keyword": "B New"}],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr(svc_mod, "call_llm", fake_call_llm)

        async def fake_search(keyword):
            return {"id": "new", "name": "New", "artist": "B",
                    "cover_url": "", "album": "", "duration_ms": 111}

        monkeypatch.setattr("backend.tools.music_tool.search_first_song", fake_search)

        async def fake_mp3(song_id, level="standard"):
            return f"http://x/{song_id}.mp3"

        monkeypatch.setattr("backend.tools.netease_api.get_song_mp3_url", fake_mp3)
        monkeypatch.setattr(svc_mod, "get_song_mp3_url", fake_mp3)

        async def fake_stream(system_prompt, user_prompt, **kw):
            yield {"analysis": "", "answer": "接着听。", "actions": [],
                   "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr(svc_mod, "stream_llm", fake_stream)

        events = []
        async for sse in service.generate_reply_stream(
            "", {"Auto Continue": "true"}, session_id="auto-llm",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        music_events = [e for e in events if "event: music" in e]
        assert len(music_events) == 1
        assert '"song_id": "new"' in music_events[0]

    @pytest.mark.asyncio
    async def test_no_candidate_emits_error(self, service, monkeypatch) -> None:
        from backend.services import assistant_service as svc_mod

        ctx = service.session_manager.get_or_create("auto-none")
        ctx.memory_manager = StubMemoryManager()

        async def fake_list(session_id=None, limit=50):
            return []

        monkeypatch.setattr(service.play_history, "list_async", fake_list)

        async def fake_simi(song_id, limit=10):
            return []

        monkeypatch.setattr(svc_mod, "find_similar_songs", fake_simi)

        async def fake_call_llm(system_prompt, user_prompt, model=None, **kw):
            return {"analysis": "", "answer": "", "actions": [],
                    "play_keyword": "", "provider": "t", "model": "m"}

        monkeypatch.setattr(svc_mod, "call_llm", fake_call_llm)

        events = []
        async for sse in service.generate_reply_stream(
            "", {"Auto Continue": "true"}, session_id="auto-none",
        ):
            if sse.startswith("event: "):
                events.append(sse)

        assert any("event: error" in e for e in events)
        assert not any("event: music" in e for e in events)


class TestNoRecord:
    """数据卫生（RFC-020）—— 测试请求（No Record）不写历史/记忆/画像。"""

    def test_no_record_detection(self) -> None:
        assert AssistantService._is_no_record({"No Record": "true"}) is True
        assert AssistantService._is_no_record({"No Record": "TRUE"}) is True
        assert AssistantService._is_no_record({}) is False
        assert AssistantService._is_no_record(None) is False

    @pytest.mark.asyncio
    async def test_no_record_skips_play_history(self, service, monkeypatch) -> None:
        calls: list[str] = []

        async def fake_add(session_id, song, requested_keyword=""):
            calls.append(str(song.get("song_id")))
            return 1

        monkeypatch.setattr(service.play_history, "add_async", fake_add)

        service._record_play_history("s1", {"song_id": "a"}, {"No Record": "true"})
        service._record_play_history("s1", {"song_id": "b"}, {})
        await asyncio.sleep(0.05)

        assert calls == ["b"]
