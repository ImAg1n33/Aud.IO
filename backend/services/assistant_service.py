"""Agent orchestration: Perceive -> Decide -> Execute -> Record pipeline.

v0.4 (RFC-007): System-level prompts sent as role="system" via llm_client.
ContextAssembler now only assembles the user-prompt portion.
All prompt text lives in backend.agent.prompts.
"""

import asyncio
import json
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
from backend.memory.embedding import EmbeddingProvider
from backend.memory.episodic_memory import EpisodicMemory
from backend.services.session_manager import SessionManager
from backend.tools.netease_api import get_song_mp3_url, search_first_song


class AssistantService:
    MAX_RETRIES = 2
    SESSION_TTL = 86400
    MAX_SESSIONS = 100

    def __init__(
        self,
        episodic_db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        self.session_manager = SessionManager(ttl=self.SESSION_TTL, maxsize=self.MAX_SESSIONS)
        self.episodic_memory = EpisodicMemory(
            db_path=episodic_db_path or (backend_root / "memory" / "episodes.db"),
            embedding_provider=embedding_provider,
        )
        self.intent_classifier = IntentClassifier()
        self.tool_executor = ToolExecutor(max_retries=self.MAX_RETRIES)
        self.episodic_provider = EpisodicMemoryProvider(self.episodic_memory)

    # ================================================================
    # Session helpers
    # ================================================================

    def _resolve_session(self, session_id: str | None) -> str:
        return session_id.strip() if session_id else str(uuid.uuid4())

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
        )

        # === DECIDE + EXECUTE (with retry loop) ===
        working_input = user_input
        retry_count = 0

        while True:
            reply = await call_llm(NON_STREAMING_SYSTEM, user_prompt)

            actions = self._parse_actions_from_reply(reply)
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
            )

        # === RECORD ===
        if final_reply.get("analysis") != "Model call failed.":
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
        metadata: dict[str, Any] = dict(context or {})

        profile = ctx.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        # ═══════════════════════════════════════════════════════════
        # RFC-003 Two-Pass path — MUSIC_PLAY only
        # ═══════════════════════════════════════════════════════════
        if intent == Intent.MUSIC_PLAY:
            # ── Phase 1: Silent pre-fetch ──
            yield self._sse("status", '{"phase":"searching"}')
            song_data = await self._phase1_prefetch(user_input, sid, metadata)

            if song_data is not None:
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
                yield self._sse("done", json.dumps(final_reply, ensure_ascii=False))
                return

            # Phase 1 failed — DJ breaks the news naturally
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
        )

        reply: dict[str, Any] = {}

        async for chunk in stream_llm(SINGLE_PASS_STREAM_SYSTEM, user_prompt):
            if isinstance(chunk, str):
                yield self._sse("token", chunk)
            elif isinstance(chunk, dict):
                reply = chunk
                if reply.get("analysis") == "Model call failed.":
                    stream_interrupted = reply.get("stream_interrupted", False)
                    error_msg = reply.get("answer", "Request failed")
                    yield self._sse("error", error_msg)
                    return

        yield self._sse("text", reply.get("answer", ""))

        # === EXECUTE (with retry loop — RFC-007: streaming path now retries) ===
        actions = self._parse_actions_from_reply(reply)
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
            )

            # Retry silently via non-streaming LLM — user already saw the original text
            retry_reply = await call_llm(NON_STREAMING_SYSTEM, retry_prompt)
            retry_actions = self._parse_actions_from_reply(retry_reply)
            results = await self.tool_executor.execute_actions(retry_actions)
            final_reply = await self._merge_tool_results(retry_reply, results)

        music = final_reply.get("music")
        if isinstance(music, dict) and music.get("song_id"):
            yield self._sse("music", json.dumps(music, ensure_ascii=False))

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
    # Helpers
    # ================================================================

    @staticmethod
    def _parse_actions_from_reply(reply: dict[str, Any]) -> list[dict[str, Any]]:
        actions = reply.get("actions", [])
        if not isinstance(actions, list):
            return []

        tool_actions: list[dict[str, Any]] = []
        for item in actions:
            if isinstance(item, dict) and "tool" in item:
                tool_actions.append(item)
                continue

            if isinstance(item, str):
                stripped = item.strip()
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict) and "tool" in parsed:
                        tool_actions.append(parsed)
                        continue
                except json.JSONDecodeError:
                    pass
                try:
                    import ast
                    parsed = ast.literal_eval(stripped)
                    if isinstance(parsed, dict) and "tool" in parsed:
                        tool_actions.append(parsed)
                        continue
                except (ValueError, SyntaxError):
                    pass

        return tool_actions

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
