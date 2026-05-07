"""Agent orchestration: Perceive -> Decide -> Execute -> Record pipeline."""

import asyncio
import json
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
from backend.agent.llm_client import call_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompt_builder import ENHANCED_SYSTEM_PERSONA, ENHANCED_TOOL_CONSTRAINTS
from backend.agent.tool_executor import ToolExecutor
from backend.memory.conversation_memory import ConversationMemory
from backend.memory.episodic_memory import EpisodicMemory


class AssistantService:
    MAX_RETRIES = 2

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        episodic_db_path: Path | None = None,
    ) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        self.memory_manager = memory_manager or MemoryManager()
        self.short_term_memory = ConversationMemory(max_turns=20)
        self.episodic_memory = EpisodicMemory(
            episodic_db_path or (backend_root / "memory" / "episodes.db")
        )
        self.episodic_provider = EpisodicMemoryProvider(self.episodic_memory)
        self.intent_classifier = IntentClassifier()
        self.tool_executor = ToolExecutor(max_retries=self.MAX_RETRIES)
        self.context_assembler = ContextAssembler(
            providers=[
                ConversationHistoryProvider(self.short_term_memory),
                UserPreferenceProvider(self.memory_manager, self.episodic_memory),
                CurrentlyPlayingProvider(),
                ToolSchemaProvider(),
                self.episodic_provider,
            ],
            system_persona=ENHANCED_SYSTEM_PERSONA,
            tool_constraints=ENHANCED_TOOL_CONSTRAINTS,
        )

    async def generate_reply(
        self, user_input: str, context: dict[str, Any] | None
    ) -> tuple[dict[str, Any], str]:
        """Perceive -> Decide -> Execute -> Record pipeline.

        Returns (reply_dict, prompt_string) — same contract as before.
        """
        # === PERCEIVE ===
        intent = self.intent_classifier.classify(user_input)
        metadata: dict[str, Any] = dict(context or {})

        # Refresh mood keys from latest profile so episodic provider stays current
        profile = self.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        prompt = await self.context_assembler.assemble(user_input, intent, metadata)

        # === DECIDE + EXECUTE (with retry loop) ===
        working_input = user_input
        retry_count = 0

        while True:
            reply = await asyncio.to_thread(call_llm, prompt)

            actions = self._parse_actions_from_reply(reply)
            results = await self.tool_executor.execute_actions(actions)
            final_reply = self._merge_tool_results(reply, results)

            retry_contexts = self._collect_retry_contexts(results)
            if not retry_contexts:
                break

            if retry_count >= self.MAX_RETRIES:
                final_reply = self._build_graceful_fallback(final_reply)
                break

            retry_count += 1
            feedback = self._build_retry_feedback(final_reply, retry_contexts)
            working_input = f"{user_input}\n\n[System: {feedback}]"
            prompt = await self.context_assembler.assemble(working_input, intent, metadata)

        # === RECORD ===
        # Don't record error responses — they pollute conversation history
        if final_reply.get("analysis") != "Model call failed.":
            played_song = final_reply.get("music")
            self.short_term_memory.add_turn(
                user_input,
                final_reply.get("answer", ""),
                intent=str(intent),
                played_song=played_song,
            )

            # Fire-and-forget episodic storage (don't block the response)
            asyncio.ensure_future(
                self.episodic_memory.store_snapshot(
                    user_input,
                    final_reply.get("answer", ""),
                    played_song=played_song,
                )
            )

        return final_reply, prompt

    def schedule_profile_update(
        self,
        background_tasks: Any,
        user_input: str,
        final_reply: dict[str, Any],
    ) -> None:
        """Schedule async profile update as a background task. Unchanged from before."""
        background_tasks.add_task(
            self.memory_manager.async_update_profile,
            user_input,
            json.dumps(final_reply, ensure_ascii=False),
        )

    # ================================================================
    # Helpers
    # ================================================================

    @staticmethod
    def _parse_actions_from_reply(reply: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool action directives from LLM response.

        Accepts two formats (LLMs sometimes produce one or the other):
        1. Proper JSON: {"tool": "search_music", "keyword": "..."}
        2. String-encoded: "{'tool': 'search_music', 'keyword': '...'}"
        """
        actions = reply.get("actions", [])
        if not isinstance(actions, list):
            return []

        tool_actions: list[dict[str, Any]] = []
        for item in actions:
            if isinstance(item, dict) and "tool" in item:
                tool_actions.append(item)
                continue

            # Try to parse string-encoded dict (common LLM output bug)
            if isinstance(item, str):
                stripped = item.strip()
                # Try JSON first
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict) and "tool" in parsed:
                        tool_actions.append(parsed)
                        continue
                except json.JSONDecodeError:
                    pass
                # Try Python dict repr: {'tool': 'search_music', ...}
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
    def _merge_tool_results(
        reply: dict[str, Any], results: list[Any]
    ) -> dict[str, Any]:
        """Attach successful tool results to the reply dict.

        When a search_music result is found without a follow-up get_music_url,
        automatically resolve the mp3_url via synchronous call.
        """
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
                # search_music result — attach as music data
                merged["music"] = {
                    "requested_keyword": result.data.get("requested_keyword", ""),
                    "song_id": result.data.get("song_id", ""),
                    "name": result.data.get("name", ""),
                    "artist": result.data.get("artist", ""),
                }
                # Auto-resolve MP3 URL
                sid = result.data.get("song_id", "")
                if sid:
                    try:
                        mp3_url = get_song_mp3_url(str(sid))
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
        """Collect retry feedback messages from tool execution results."""
        contexts: list[str] = []
        for result in results:
            if hasattr(result, "metadata") and result.metadata.get("retry_context"):
                contexts.append(str(result.metadata["retry_context"]))
        return contexts

    @staticmethod
    def _build_retry_feedback(reply: dict[str, Any], retry_contexts: list[str]) -> str:
        """Build feedback message for the LLM retry loop."""
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
        """Build a gracefully degraded reply after all retries exhausted."""
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
