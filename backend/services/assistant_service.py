"""Agent orchestration: Perceive -> Decide -> Execute -> Record pipeline.

v0.3 升级:
- 引入 SessionManager (TTLCache) 实现多用户会话隔离
- ConversationMemory 和 MemoryManager 按 session_id 独立
- EpisodicMemory 共享实例，通过 session_id 过滤检索
- ContextAssembler 每请求重建，携带当前会话的 provider 引用
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
    EpisodicMemoryProvider,
    ToolSchemaProvider,
    UserPreferenceProvider,
)
from backend.agent.intent_classifier import IntentClassifier
from backend.agent.llm_client import call_llm, stream_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompt_builder import ENHANCED_SYSTEM_PERSONA, ENHANCED_TOOL_CONSTRAINTS
from backend.agent.tool_executor import ToolExecutor
from backend.memory.embedding import EmbeddingProvider
from backend.memory.episodic_memory import EpisodicMemory
from backend.services.session_manager import SessionManager


class AssistantService:
    MAX_RETRIES = 2
    SESSION_TTL = 86400   # 24 hours idle → evict
    MAX_SESSIONS = 100

    def __init__(
        self,
        episodic_db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        self.session_manager = SessionManager(ttl=self.SESSION_TTL, maxsize=self.MAX_SESSIONS)
        # EpisodicMemory is shared across sessions (filtered by session_id at query time)
        self.episodic_memory = EpisodicMemory(
            db_path=episodic_db_path or (backend_root / "memory" / "episodes.db"),
            embedding_provider=embedding_provider,
        )
        self.intent_classifier = IntentClassifier()
        self.tool_executor = ToolExecutor(max_retries=self.MAX_RETRIES)
        # EpisodicMemoryProvider is reusable (references shared episodic_memory)
        self.episodic_provider = EpisodicMemoryProvider(self.episodic_memory)

    # ================================================================
    # Session helpers
    # ================================================================

    def _resolve_session(self, session_id: str | None) -> str:
        """Normalise session_id — generate UUID if missing."""
        return session_id.strip() if session_id else str(uuid.uuid4())

    def _ensure_memory_manager(self, session_id: str) -> None:
        """Lazily initialise MemoryManager for a session if not yet created."""
        ctx = self.session_manager.get_or_create(session_id)
        if ctx.memory_manager is None:
            ctx.memory_manager = MemoryManager(session_id=session_id)

    def _build_context_assembler(
        self,
        session_id: str,
    ) -> ContextAssembler:
        """Create a ContextAssembler wired to the current session's state."""
        self._ensure_memory_manager(session_id)
        ctx = self.session_manager.get_or_create(session_id)

        return ContextAssembler(
            providers=[
                ConversationHistoryProvider(ctx.short_term_memory),
                UserPreferenceProvider(ctx.memory_manager, self.episodic_memory),
                CurrentlyPlayingProvider(),
                ToolSchemaProvider(),
                self.episodic_provider,
            ],
            system_persona=ENHANCED_SYSTEM_PERSONA,
            tool_constraints=ENHANCED_TOOL_CONSTRAINTS,
        )

    # ================================================================
    # Non-streaming pipeline
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
        intent = self.intent_classifier.classify(user_input)
        metadata: dict[str, Any] = dict(context or {})

        # Refresh mood keys from per-session profile
        profile = ctx.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        assembler = self._build_context_assembler(sid)
        prompt = await assembler.assemble(user_input, intent, metadata)

        # === DECIDE + EXECUTE (with retry loop) ===
        working_input = user_input
        retry_count = 0

        while True:
            reply = await call_llm(prompt)

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
            feedback = self._build_retry_feedback(final_reply, retry_contexts)
            working_input = f"{user_input}\n\n[System: {feedback}]"
            prompt = await assembler.assemble(working_input, intent, metadata)

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

        return final_reply, prompt

    # ================================================================
    # Streaming pipeline
    # ================================================================

    async def generate_reply_stream(
        self, user_input: str, context: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming variant: yields SSE events for real-time typewriter UX."""
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        self._ensure_memory_manager(sid)

        # === PERCEIVE ===
        intent = self.intent_classifier.classify(user_input)
        metadata: dict[str, Any] = dict(context or {})

        profile = ctx.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        assembler = self._build_context_assembler(sid)
        prompt = await assembler.assemble(user_input, intent, metadata)

        # === DECIDE (streaming with retry) ===
        reply: dict[str, Any] = {}
        stream_interrupted: bool = False

        async for chunk in stream_llm(prompt):
            if isinstance(chunk, str):
                yield self._sse("token", chunk)
            elif isinstance(chunk, dict):
                reply = chunk
                if reply.get("analysis") == "Model call failed.":
                    stream_interrupted = reply.get("stream_interrupted", False)
                    error_msg = reply.get("answer", "Request failed")
                    if stream_interrupted:
                        yield self._sse("error", error_msg)
                    else:
                        yield self._sse("error", error_msg)
                    return

        if stream_interrupted or reply.get("analysis") == "Model call failed.":
            return

        yield self._sse("text", reply.get("answer", ""))

        # === EXECUTE ===
        actions = self._parse_actions_from_reply(reply)
        results = await self.tool_executor.execute_actions(actions)
        final_reply = await self._merge_tool_results(reply, results)

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
    # Helpers (unchanged)
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
    def _build_retry_feedback(reply: dict[str, Any], retry_contexts: list[str]) -> str:
        music = reply.get("music")
        song_name = ""
        if isinstance(music, dict):
            song_name = music.get("name", "") or music.get("requested_keyword", "")

        target = song_name or "the requested song"
        feedback = " ".join(retry_contexts)
        if target and target not in feedback:
            feedback = f"The song '{target}' could not be played. {feedback}"
        return feedback

    @staticmethod
    def _build_graceful_fallback(reply: dict[str, Any]) -> dict[str, Any]:
        degraded = dict(reply)
        degraded.pop("music", None)
        degraded["play_keyword"] = ""

        fallback_text = (
            "Sorry, I picked several songs but all were blocked by copyright restrictions. "
            "Please try a different style or specify a different artist."
        )
        degraded["answer"] = fallback_text
        degraded["say"] = fallback_text

        actions = degraded.get("actions")
        if isinstance(actions, list):
            degraded["actions"] = [
                item for item in actions
                if not (isinstance(item, dict) and item.get("tool") in {"search_music", "get_music_url"})
            ]

        return degraded
