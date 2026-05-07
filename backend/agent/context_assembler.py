"""Dynamic context assembly with pluggable providers, replacing static prompt_builder."""

import json
from abc import ABC, abstractmethod
from typing import Any

from backend.agent.intent_classifier import Intent
from backend.agent.memory_manager import MemoryManager
from backend.memory.conversation_memory import ConversationMemory
from backend.memory.episodic_memory import EpisodicMemory
from backend.tools.base import tool_registry


# ============================================================
# Provider interface
# ============================================================

class ContextProvider(ABC):
    name: str = ""

    @abstractmethod
    async def get_context(
        self,
        intent: Intent,
        user_input: str,
        metadata: dict[str, Any],
    ) -> str | None:
        """Return a context block string, or None to skip this provider."""


# ============================================================
# Built-in providers
# ============================================================

class ConversationHistoryProvider(ContextProvider):
    name = "conversation_history"

    def __init__(self, memory: ConversationMemory) -> None:
        self._memory = memory

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        formatted = self._memory.format_history(last_n=10)
        if not formatted:
            return None
        return f"[Previous conversation]\n{formatted}"


class UserPreferenceProvider(ContextProvider):
    name = "user_preference"

    def __init__(self, memory_manager: MemoryManager) -> None:
        self._manager = memory_manager

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        # Only inject for music intents
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        summary = self._manager.get_preference_summary()
        if not summary:
            return None

        lines = [
            "[User Music Profile — use this to personalize recommendations]",
            summary,
            "",
            "How to use this profile:",
            "- If core_taste lists genres, prefer songs in those genres when the user is open-ended.",
            "- If artist_preference has liked artists, mention/pick them when relevant.",
            "- Avoid disliked artists and genres.",
            "- If mood_bias is present and the user's mood or weather context matches a mood key, use those genres.",
            "- Never say 'based on your profile' or 'I see you like' — just naturally pick fitting music.",
        ]
        return "\n".join(lines)


class CurrentlyPlayingProvider(ContextProvider):
    name = "currently_playing"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        currently_playing = metadata.get("Currently Playing")
        if not currently_playing or currently_playing == "None":
            return None
        return f"[Currently Playing]\n{currently_playing}"


class ToolSchemaProvider(ContextProvider):
    name = "tool_schemas"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        schemas = tool_registry.get_schemas()
        if not schemas:
            return None

        tool_desc = [
            "Available tools (use in the 'actions' array of your response):",
            "Each action must be: {\"tool\": \"<tool_name>\", \"<param>\": \"<value>\", ...}",
        ]
        for schema in schemas:
            tool_desc.append(f"  - {schema['name']}: {schema['description']}")

        tool_desc.append(
            "Example: {\"tool\": \"search_music\", \"keyword\": \"Artist Song Title\"}"
        )
        return "\n".join(tool_desc)


class EpisodicMemoryProvider(ContextProvider):
    name = "episodic_memory"

    def __init__(self, episodic: EpisodicMemory) -> None:
        self._episodic = episodic

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        # Check if user references past interactions
        temporal_signals = [
            "上次", "昨天", "之前", "上次那个", "上次那首",
            "last time", "yesterday", "before", "last song", "previous",
            "again", "再", "又",
        ]
        if not any(sig in user_input for sig in temporal_signals):
            return None

        snapshots = await self._episodic.query_recent(limit=3)
        if not snapshots:
            return None

        lines = ["[Past interactions — you may reference these naturally]"]
        for snap in snapshots:
            song_info = ""
            if snap.played_song_name:
                song_info = f" [played: {snap.played_song_artist or ''} - {snap.played_song_name}]"
            lines.append(
                f"- User: {snap.user_input} | Aud.IO: {snap.assistant_reply[:120]}{song_info}"
            )
        return "\n".join(lines)


# ============================================================
# Assembler
# ============================================================

class ContextAssembler:
    def __init__(
        self,
        providers: list[ContextProvider],
        system_persona: str,
        tool_constraints: str,
    ) -> None:
        self.providers = providers
        self.system_persona = system_persona
        self.tool_constraints = tool_constraints

    async def assemble(
        self,
        user_input: str,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build the full prompt by calling each provider and concatenating non-None results."""
        meta = dict(metadata or {})
        context_blocks: list[str] = []

        for provider in self.providers:
            try:
                block = await provider.get_context(intent, user_input, meta)
                if block:
                    context_blocks.append(block)
            except Exception:
                # A provider should not crash the entire assembly
                continue

        context_text = "\n\n".join(context_blocks) if context_blocks else "- none"

        raw_context = meta.get("raw_context", {})
        raw_lines = [f"- {key}: {_context_to_text(value)}" for key, value in raw_context.items()]
        raw_block = "\n".join(raw_lines) if raw_lines else ""

        sections = [
            self.system_persona,
            self.tool_constraints,
        ]

        if raw_block:
            sections.append(f"Additional Context:\n{raw_block}")

        sections.append(f"Context:\n{context_text}")
        sections.append(f"User:\n{user_input}")

        return "\n\n".join(sections)


def _context_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
