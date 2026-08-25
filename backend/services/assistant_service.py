"""Agent orchestration: Perceive -> Decide -> Execute -> Record pipeline.

v0.4 (RFC-007): System-level prompts sent as role="system" via llm_client.
ContextAssembler now only assembles the user-prompt portion.
All prompt text lives in backend.agent.prompts.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from backend.agent.context_assembler import (
    ContextAssembler,
    ConversationHistoryProvider,
    CurrentlyPlayingProvider,
    EnvironmentProvider,
    EpisodicMemoryProvider,
    SessionSummaryProvider,
    ToolSchemaProvider,
    UserPreferenceProvider,
)
from backend.agent.intent_classifier import Intent, IntentClassifier
from backend.agent.llm_client import call_llm, stream_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompts import (
    GRACEFUL_FALLBACK_TEXT,
    NON_STREAMING_SYSTEM,
    PHASE1_DECISION_SYSTEM,
    PHASE2_STREAM_SYSTEM,
    SINGLE_PASS_STREAM_SYSTEM,
    TOOL_CONSTRAINTS,
    build_phase1_fail_user_prompt,
    build_phase1_user_prompt,
    build_retry_feedback,
)
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tts_provider import TTSProvider
from backend.memory.embedding import EmbeddingProvider
from backend.memory.episodic_memory import EpisodicMemory
from backend.memory.reflection import SessionReflector
from backend.services.session_manager import SessionManager
from backend.tools.netease_api import get_song_mp3_url, search_first_song

logger = logging.getLogger(__name__)


class AssistantService:
    MAX_RETRIES = 2
    SESSION_TTL = 86400
    MAX_SESSIONS = 100
    REFLECT_EVERY_TURNS = 10  # Reflection: 每 N 轮对话压一次会话摘要

    def __init__(
        self,
        episodic_db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.session_manager = SessionManager(ttl=self.SESSION_TTL, maxsize=self.MAX_SESSIONS)
        self.episodic_memory = EpisodicMemory(
            db_path=episodic_db_path,  # None triggers data_config defaults
            embedding_provider=embedding_provider,
        )
        self.intent_classifier = IntentClassifier()
        self.tool_executor = ToolExecutor(max_retries=self.MAX_RETRIES)
        self.episodic_provider = EpisodicMemoryProvider(self.episodic_memory)
        self.summary_provider = SessionSummaryProvider(self.episodic_memory)
        self.reflector = SessionReflector(self.episodic_memory)
        self.tts = TTSProvider()

    # ================================================================
    # Session helpers
    # ================================================================

    def _resolve_session(self, session_id: str | None) -> str:
        return session_id.strip() if session_id else str(uuid.uuid4())

    def _build_tool_schemas(self, intent: Intent) -> list[dict[str, Any]]:
        """按意图门控返回 OpenAI function schemas（RFC: function calling 重构）。

        MUSIC_PLAY / MUSIC_RECOMMEND → 音乐类工具
        WEATHER / CHITCHAT           → 不暴露工具（避免误导调用）
        UNKNOWN                      → 全部可用工具
        通用类工具（如 MCP 挂载）在音乐意图下同样可见。
        """
        categories = set(self.intent_classifier.should_activate_tool_categories(intent))
        if not categories:
            return []
        from backend.tools.base import tool_registry

        tools = [
            t for t in tool_registry.get_available()
            if t.category in categories or t.category == "general"
        ]
        return [t.to_openai_function_schema() for t in tools]

    def _ensure_memory_manager(self, session_id: str) -> None:
        ctx = self.session_manager.get_or_create(session_id)
        if ctx.memory_manager is None:
            ctx.memory_manager = MemoryManager(session_id=session_id)

    def _build_context_assembler(self, session_id: str) -> ContextAssembler:
        """Full assembler for Single-Pass (CHITCHAT, WEATHER, UNKNOWN)."""
        self._ensure_memory_manager(session_id)
        ctx = self.session_manager.get_or_create(session_id)

        return ContextAssembler(
            providers=[
                EnvironmentProvider(),
                ConversationHistoryProvider(ctx.short_term_memory),
                SessionSummaryProvider(self.episodic_memory),
                UserPreferenceProvider(ctx.memory_manager, self.episodic_memory),
                CurrentlyPlayingProvider(),
                ToolSchemaProvider(),
                self.episodic_provider,
            ],
        )

    def _build_phase2_assembler(self, session_id: str) -> ContextAssembler:
        """Ultra-light assembler for Two-Pass Phase 2.

        Song is already resolved — only environment + short conversation
        history needed for continuity.
        """
        self._ensure_memory_manager(session_id)
        ctx = self.session_manager.get_or_create(session_id)

        return ContextAssembler(
            providers=[
                EnvironmentProvider(),
                ConversationHistoryProvider(ctx.short_term_memory),
            ],
        )

    # ================================================================
    # Non-streaming pipeline (legacy)
    # ================================================================

    async def generate_reply(
        self, user_input: str, context: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Perceive -> Decide -> Execute -> Record pipeline."""
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        self._ensure_memory_manager(sid)

        # === PERCEIVE ===
        intent = await self.intent_classifier.classify_async(user_input)
        metadata: dict[str, Any] = dict(context or {})

        profile = ctx.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        assembler = self._build_context_assembler(sid)
        user_prompt = await assembler.assemble(
            user_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
            session_id=sid,
        )

        # === DECIDE + EXECUTE (with retry loop) ===
        working_input = user_input
        retry_count = 0
        tools = self._build_tool_schemas(intent)
        force_tools = intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

        while True:
            reply = await call_llm(
                NON_STREAMING_SYSTEM, user_prompt,
                tools=tools, force_tools=force_tools,
            )

            actions = self._parse_actions_from_reply(reply)

            # tool_choice=required 偶发不被遵守——音乐类意图无工具调用时强制重试一次
            if force_tools and not actions and tools:
                strict_input = (
                    f"{working_input}\n\n[System: 你必须调用 search_music 或 get_music_url "
                    "工具实际搜索。不要假设版权问题——先搜索，搜到就能放。]"
                )
                strict_prompt = await assembler.assemble(
                    strict_input, intent, metadata,
                    tool_constraints=TOOL_CONSTRAINTS, session_id=sid,
                )
                strict_reply = await call_llm(
                    NON_STREAMING_SYSTEM, strict_prompt,
                    tools=tools, force_tools=True,
                )
                strict_actions = self._parse_actions_from_reply(strict_reply)
                if strict_actions:
                    reply = strict_reply
                    actions = strict_actions

            results = await self.tool_executor.execute_actions(actions)
            final_reply = await self._merge_tool_results(reply, results)

            retry_contexts = self._collect_retry_contexts(results)
            if not retry_contexts:
                break

            if retry_count >= self.MAX_RETRIES:
                final_reply = self._build_graceful_fallback(final_reply)
                break

            retry_count += 1
            feedback = build_retry_feedback(final_reply, retry_contexts)
            working_input = f"{user_input}\n\n[System: {feedback}]"
            user_prompt = await assembler.assemble(
                working_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
                session_id=sid,
            )

        # === RECORD ===
        if final_reply.get("analysis") != "Model call failed.":
            final_reply = self._ensure_dj_line(final_reply)
            played_song = final_reply.get("music")
            ctx.short_term_memory.add_turn(
                user_input,
                final_reply.get("answer", ""),
                intent=str(intent),
                played_song=played_song,
            )

            asyncio.ensure_future(
                self.episodic_memory.store_snapshot(
                    user_input,
                    final_reply.get("answer", ""),
                    played_song=played_song,
                    session_id=sid,
                )
            )

        self._maybe_reflect(sid, ctx)
        return final_reply, user_prompt

    # ================================================================
    # Streaming pipeline
    # ================================================================

    async def generate_reply_stream(
        self, user_input: str, context: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming variant — Two-Pass for music, Single-Pass for everything else.

        RFC-003: Two-Pass pipeline for MUSIC_PLAY.
        RFC-007: System prompts sent as role="system".
        """
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        self._ensure_memory_manager(sid)

        # === PERCEIVE ===
        intent = await self.intent_classifier.classify_async(user_input)
        logger.debug("Intent: %s, input: %.60s...", intent.value, user_input)
        metadata: dict[str, Any] = dict(context or {})

        # ═══════════════════════════════════════════════════════════
        # RFC-003 Two-Pass path — MUSIC_PLAY only
        # ═══════════════════════════════════════════════════════════
        if intent == Intent.MUSIC_PLAY:
            # ── Phase 1: Silent pre-fetch ──
            yield self._sse("status", '{"phase":"searching"}')
            song_data = await self._phase1_prefetch(user_input, sid, metadata)

            if song_data is not None:
                logger.info(
                    "Phase 1 OK: '%s' → %s - %s",
                    user_input, song_data["artist"], song_data["name"],
                )
                # ── Phase 2: Radio DJ timing ──
                yield self._sse(
                    "status",
                    json.dumps({
                        "phase": "found",
                        "name": song_data["name"],
                        "artist": song_data["artist"],
                    }, ensure_ascii=False),
                )

                # Music first — like a DJ dropping the track before speaking
                yield self._sse("music", json.dumps(song_data, ensure_ascii=False))

                # Build user-prompt while music plays
                assembler = self._build_phase2_assembler(sid)
                user_prompt = await assembler.assemble(
                    user_input, intent, metadata, resolved_song=song_data,
                    session_id=sid,
                )

                # Stream DJ script over the already-playing music
                reply = {}
                async for chunk in stream_llm(PHASE2_STREAM_SYSTEM, user_prompt):
                    if isinstance(chunk, str):
                        yield self._sse("token", chunk)
                    elif isinstance(chunk, dict):
                        reply = chunk
                        if reply.get("analysis") == "Model call failed.":
                            yield self._sse("error", reply.get("answer", "Request failed"))
                            return

                yield self._sse("text", reply.get("answer", ""))

                # TTS pre-roll tag — music is already playing, speech is non-blocking
                speech_sse = await self._maybe_yield_speech(
                    reply.get("answer", ""), intent.value, is_music_play=True,
                )
                if speech_sse:
                    yield speech_sse

                final_reply = {
                    **reply,
                    "music": song_data,
                    "provider": reply.get("provider", ""),
                    "model": reply.get("model", ""),
                }
                ctx.short_term_memory.add_turn(
                    user_input, reply.get("answer", ""),
                    intent=str(intent), played_song=song_data,
                )
                asyncio.ensure_future(
                    self.episodic_memory.store_snapshot(
                        user_input, reply.get("answer", ""),
                        played_song=song_data, session_id=sid,
                    )
                )
                self._maybe_reflect(sid, ctx)
                yield self._sse("done", json.dumps(final_reply, ensure_ascii=False))
                return

            # Phase 1 failed — DJ breaks the news naturally
            logger.info("Phase 1 miss: '%s'", user_input)
            yield self._sse("status", '{"phase":"not_found"}')

            fail_user_prompt = build_phase1_fail_user_prompt(user_input)
            reply = {}
            async for chunk in stream_llm(SINGLE_PASS_STREAM_SYSTEM, fail_user_prompt):
                if isinstance(chunk, str):
                    yield self._sse("token", chunk)
                elif isinstance(chunk, dict):
                    reply = chunk
            yield self._sse("text", reply.get("answer", ""))
            yield self._sse("done", json.dumps(reply, ensure_ascii=False))
            return

        # ═══════════════════════════════════════════════════════════
        # Single-Pass path — CHITCHAT, WEATHER, UNKNOWN, MUSIC_RECOMMEND
        # ═══════════════════════════════════════════════════════════
        assembler = self._build_context_assembler(sid)
        user_prompt = await assembler.assemble(
            user_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
            session_id=sid,
        )
        tools = self._build_tool_schemas(intent)
        force_tools = intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

        reply: dict[str, Any] = {}

        async for chunk in stream_llm(
            SINGLE_PASS_STREAM_SYSTEM, user_prompt,
            tools=tools, force_tools=force_tools,
        ):
            if isinstance(chunk, str):
                yield self._sse("token", chunk)
            elif isinstance(chunk, dict):
                reply = chunk
                if reply.get("analysis") == "Model call failed.":
                    error_msg = reply.get("answer", "Request failed")
                    yield self._sse("error", error_msg)
                    return

        yield self._sse("text", reply.get("answer", ""))

        # === EXECUTE (with retry loop — RFC-007: streaming path now retries) ===
        actions = self._parse_actions_from_reply(reply)

        # tool_choice=required 偶发不被遵守——模型可能只输出文本，甚至凭空臆想
        # "版权锁了"。音乐类意图无工具调用 → 强制重试一次，纠正"先搜索再判断"。
        if force_tools and not actions and tools:
            strict_input = (
                f"{user_input}\n\n[System: 你必须调用 search_music 或 get_music_url "
                "工具实际搜索。不要假设版权问题——先搜索，搜到就能放。]"
            )
            strict_prompt = await assembler.assemble(
                strict_input, intent, metadata,
                tool_constraints=TOOL_CONSTRAINTS, session_id=sid,
            )
            strict_reply = await call_llm(
                NON_STREAMING_SYSTEM, strict_prompt,
                tools=tools, force_tools=True,
            )
            strict_actions = self._parse_actions_from_reply(strict_reply)
            if strict_actions:
                reply = strict_reply
                actions = strict_actions

        results = await self.tool_executor.execute_actions(actions)
        final_reply = await self._merge_tool_results(reply, results)

        retry_count = 0
        while self._collect_retry_contexts(results):
            if retry_count >= self.MAX_RETRIES:
                final_reply = self._build_graceful_fallback(final_reply)
                break

            retry_count += 1
            feedback = build_retry_feedback(final_reply, self._collect_retry_contexts(results))
            retry_input = f"{user_input}\n\n[System: {feedback}]"
            retry_prompt = await assembler.assemble(
                retry_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
                session_id=sid,
            )

            # Retry silently via non-streaming LLM — user already saw the original text
            retry_reply = await call_llm(
                NON_STREAMING_SYSTEM, retry_prompt,
                tools=tools, force_tools=force_tools,
            )
            retry_actions = self._parse_actions_from_reply(retry_reply)
            results = await self.tool_executor.execute_actions(retry_actions)
            final_reply = await self._merge_tool_results(retry_reply, results)

        music = final_reply.get("music")
        if isinstance(music, dict) and music.get("song_id"):
            yield self._sse("music", json.dumps(music, ensure_ascii=False))

        # 工具 required 模式下文案可能为空 → 模板兜底（不空场）
        final_reply = self._ensure_dj_line(final_reply)
        # 兜底文案补发 text 事件（此前 text 事件用 LLM 原文，可能为空）
        if final_reply.get("answer", "").strip() and not reply.get("answer", "").strip():
            yield self._sse("text", final_reply["answer"])

        # TTS — for CHITCHAT/WEATHER this is the main audio output (no music)
        speech_sse = await self._maybe_yield_speech(
            final_reply.get("answer", ""), intent.value,
        )
        if speech_sse:
            yield speech_sse

        # === RECORD ===
        played_song = final_reply.get("music")
        ctx.short_term_memory.add_turn(
            user_input,
            final_reply.get("answer", ""),
            intent=str(intent),
            played_song=played_song,
        )
        asyncio.ensure_future(
            self.episodic_memory.store_snapshot(
                user_input,
                final_reply.get("answer", ""),
                played_song=played_song,
                session_id=sid,
            )
        )
        self._maybe_reflect(sid, ctx)

        yield self._sse("done", json.dumps(final_reply, ensure_ascii=False))

    # ═══════════════════════════════════════════════════════════════
    # RFC-003 helpers
    # ═══════════════════════════════════════════════════════════════

    async def _phase1_prefetch(
        self, user_input: str, sid: str, metadata: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Phase 1: silently extract play_keyword -> search -> resolve mp3_url.

        Uses PHASE1_DECISION_SYSTEM (role="system") with minimal context
        (role="user") to disambiguate song-title vs emotion.
        """
        ctx = self.session_manager.get_or_create(sid)
        currently_playing = (metadata or {}).get("Currently Playing", "")
        last_turn = ctx.short_term_memory.get_last_user_message() or ""
        last_reply = ctx.short_term_memory.get_last_assistant_reply() or ""

        decision_user_prompt = build_phase1_user_prompt(
            user_input,
            currently_playing=currently_playing if currently_playing != "None" else "",
            last_turn=last_turn,
            last_reply=last_reply,
        )

        try:
            decision = await call_llm(PHASE1_DECISION_SYSTEM, decision_user_prompt)
        except Exception:
            return None

        play_kw = (decision.get("play_keyword") or "").strip()
        if not play_kw:
            return None

        try:
            search_result = await search_first_song(play_kw)
            mp3_url = await get_song_mp3_url(search_result["id"])
            return {
                "song_id": search_result["id"],
                "name": search_result["name"],
                "artist": search_result["artist"],
                "mp3_url": mp3_url,
                "requested_keyword": play_kw,
            }
        except Exception:
            return None

    @staticmethod
    def _sse(event: str, data: str) -> str:
        return f"event: {event}\ndata: {data}\n\n"

    async def _maybe_yield_speech(
        self, answer_text: str, intent_str: str, is_music_play: bool = False,
    ) -> str | None:
        """Synthesize TTS speech and return the SSE string, or None on skip/fail.

        MUSIC_PLAY: short pre-roll (≤80 chars).  CHITCHAT/WEATHER: full answer.
        """
        if not self.tts.is_enabled or not self.tts.intent_enabled(intent_str):
            return None

        text = (
            self.tts.pre_roll_text(answer_text, max_len=80)
            if is_music_play
            else (answer_text or "").strip()
        )
        if not text:
            return None

        urls = await self.tts.synthesize(text)
        return self._sse("speech", json.dumps({
            "urls": urls,
            "text": text,
            "intent": intent_str,
        }, ensure_ascii=False))

    def schedule_profile_update(
        self,
        background_tasks: Any,
        user_input: str,
        final_reply: dict[str, Any],
        session_id: str | None = None,
    ) -> None:
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        background_tasks.add_task(
            ctx.memory_manager.async_update_profile,
            user_input,
            json.dumps(final_reply, ensure_ascii=False),
        )

    # ================================================================
    # Reflection（v5）—— 每 N 轮把对话压成跨会话摘要
    # ================================================================

    def _maybe_reflect(self, session_id: str, ctx: Any) -> None:
        """每 REFLECT_EVERY_TURNS 轮触发一次会话摘要（fire-and-forget）。

        用 ctx.last_summary_turn 节流：即使多次请求并发也只会触发一次。
        失败静默（Reflector 内部已降级），不阻断对话。
        """
        turn_count = len(ctx.short_term_memory)
        if turn_count < self.REFLECT_EVERY_TURNS:
            return
        if (turn_count - ctx.last_summary_turn) < self.REFLECT_EVERY_TURNS:
            return
        ctx.last_summary_turn = turn_count

        transcript = ctx.short_term_memory.format_history()
        if not transcript.strip():
            return
        try:
            asyncio.ensure_future(
                self.reflector.summarize_and_store(
                    session_id, transcript, turn_count,
                )
            )
        except Exception:
            logger.warning("Reflection 调度失败（不影响对话）", exc_info=True)

    # ================================================================
    # Helpers
    # ================================================================

    @staticmethod
    def _ensure_dj_line(reply: dict[str, Any]) -> dict[str, Any]:
        """工具调用为 required 时模型可能只输出 tool_calls 不写文案。

        若最终回答为空但音乐已解析，用确定性模板补一句 DJ 台词，
        保证打字机效果与文案展示不空场。
        """
        if reply.get("answer", "").strip():
            return reply
        music = reply.get("music")
        if isinstance(music, dict) and music.get("name"):
            name = music.get("name", "")
            artist = music.get("artist", "")
            reply["answer"] = (
                f"来一首 {artist} 的《{name}》。" if artist else f"来一首《{name}》。"
            )
            reply["analysis"] = ""
        return reply

    @staticmethod
    def _parse_actions_from_reply(reply: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize reply → tool action dicts.

        RFC: function calling 重构后，LLM 返回的 tool_calls 已在 llm_client
        归一化为 {"tool": name, ...args} 形式，这里只做类型过滤（保留
        向后兼容：旧的 "actions" 文本格式不再解析，ast 兜底已删除）。
        """
        actions = reply.get("actions", [])
        if not isinstance(actions, list):
            return []
        return [item for item in actions if isinstance(item, dict) and "tool" in item]

    @staticmethod
    async def _merge_tool_results(
        reply: dict[str, Any], results: list[Any]
    ) -> dict[str, Any]:
        from backend.tools.netease_api import get_song_mp3_url

        merged = dict(reply)
        for result in results:
            if not hasattr(result, "success"):
                continue
            if not result.success:
                continue
            if not result.data:
                continue

            if "song_id" in result.data and "mp3_url" not in result.data:
                merged["music"] = {
                    "requested_keyword": result.data.get("requested_keyword", ""),
                    "song_id": result.data.get("song_id", ""),
                    "name": result.data.get("name", ""),
                    "artist": result.data.get("artist", ""),
                }
                sid = result.data.get("song_id", "")
                if sid:
                    try:
                        mp3_url = await get_song_mp3_url(str(sid))
                        merged["music"]["mp3_url"] = mp3_url
                    except Exception:
                        merged["music"]["mp3_url"] = ""

            elif "mp3_url" in result.data:
                if "music" in merged and isinstance(merged["music"], dict):
                    merged["music"]["mp3_url"] = result.data.get("mp3_url", "")
                else:
                    merged["music"] = result.data

        return merged

    @staticmethod
    def _collect_retry_contexts(results: list[Any]) -> list[str]:
        contexts: list[str] = []
        for result in results:
            if hasattr(result, "metadata") and result.metadata.get("retry_context"):
                contexts.append(str(result.metadata["retry_context"]))
        return contexts

    @staticmethod
    def _build_graceful_fallback(reply: dict[str, Any]) -> dict[str, Any]:
        degraded = dict(reply)
        degraded.pop("music", None)
        degraded["play_keyword"] = ""

        degraded["answer"] = GRACEFUL_FALLBACK_TEXT
        degraded["say"] = GRACEFUL_FALLBACK_TEXT

        actions = degraded.get("actions")
        if isinstance(actions, list):
            degraded["actions"] = [
                item for item in actions
                if not (isinstance(item, dict) and item.get("tool") in {"search_music", "get_music_url"})
            ]

        return degraded
