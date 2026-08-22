"""Dynamic context assembly with pluggable providers.

v0.4 (RFC-007): ContextAssembler now only assembles the user-prompt portion
(context blocks + user input).  System-level prompts (identity, task, output
schema) are handled by callers via prompts.py and sent as role="system".
"""
import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import httpx
from backend.agent.intent_classifier import Intent
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompts import format_resolved_song
from backend.config import settings
from backend.memory.conversation_memory import ConversationMemory
from backend.memory.episodic_memory import EpisodicMemory, EpisodicSnapshot
from backend.tools.base import tool_registry

logger = logging.getLogger(__name__)

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

    def __init__(self, memory_manager: MemoryManager, episodic: EpisodicMemory | None = None) -> None:
        self._manager = memory_manager
        self._episodic = episodic

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        summary = self._manager.get_preference_summary()

        stats_block: str | None = None
        if self._episodic:
            sid = metadata.get("_session_id") if isinstance(metadata, dict) else None
            stats = await self._episodic.get_preference_stats(session_id=sid)
            stats_block = self._episodic.format_stats_for_prompt(stats)

        if not summary and not stats_block:
            return None

        lines = ["[User Music Profile]"]
        if summary:
            lines.append(summary)

        if stats_block:
            lines.append("")
            lines.append(stats_block)

        return "\n".join(lines)


class CurrentlyPlayingProvider(ContextProvider):
    """Injects the frontend's currently-playing track into the LLM context."""

    name = "currently_playing"
    KEY = "Currently Playing"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        currently_playing = metadata.get(self.KEY)
        if not currently_playing or currently_playing == "None":
            return None
        return f"[Currently Playing]\n{currently_playing}"


# ============================================================
# RFC-005: Environment Provider — weather + time of day
# ============================================================

_weather_cache: dict[str, Any] = {"data": "", "ts": 0.0}
_WEATHER_CACHE_TTL = 1800
_WEATHER_TIMEOUT = 1.5


async def _fetch_weather(city: str) -> str:
    query = city.strip() or ""
    url = f"https://wttr.in/{query}?format=%C+%t" if query else "https://wttr.in/?format=%C+%t"
    try:
        async with httpx.AsyncClient(timeout=_WEATHER_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "Aud.IO/0.3"})
            resp.raise_for_status()
            return resp.text.strip()
    except Exception:
        return ""


async def _get_weather_cached() -> str:
    now = time.monotonic()
    stale = (now - _weather_cache["ts"]) >= _WEATHER_CACHE_TTL
    city = settings.weather_city.strip()

    if not _weather_cache["data"]:
        asyncio.ensure_future(_refresh_weather(city))
        return ""
    elif stale:
        asyncio.ensure_future(_refresh_weather(city))

    return _weather_cache["data"]


async def _refresh_weather(city: str) -> None:
    data = await _fetch_weather(city)
    if data:
        _weather_cache["data"] = data
        _weather_cache["ts"] = time.monotonic()


def _time_period() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    return "night"


class EnvironmentProvider(ContextProvider):
    """Injects current time and weather into the LLM context."""

    name = "environment"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        period = _time_period()
        weather = await _get_weather_cached()

        parts = [f"Time: {period}"]
        if weather:
            parts.append(f"Weather: {weather}")

        lines = "[Current Environment]\n" + "\n".join(parts)
        lines += (
            "\n(Use the time of day naturally in your greeting. "
            "Reference weather ONLY if it genuinely fits the mood — "
            "never force it or invent details like wind/rain that aren't shown above.)"
        )
        return lines


class ToolSchemaProvider(ContextProvider):
    name = "tool_schemas"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        schemas = tool_registry.get_schemas()
        if not schemas:
            return None

        tool_desc = ["[Available tools]"]
        for schema in schemas:
            tool_desc.append(f"- {schema['name']}: {schema['description']}")
        return "\n".join(tool_desc)


class EpisodicMemoryProvider(ContextProvider):
    """情节记忆上下文提供者 —— 注入与当前用户意图语义相似的历史交互。

    v2.0: 主路径使用 query_by_semantic() 做向量语义检索。
    Mood 检测统一由 MoodDetector 负责 —— 此处不再维护重复的词表。
    """

    name = "episodic_memory"

    def __init__(self, episodic: EpisodicMemory) -> None:
        self._episodic = episodic

    async def get_context(
        self, intent: Intent, user_input: str, metadata: dict[str, Any]
    ) -> str | None:
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        sid = metadata.get("_session_id") if isinstance(metadata, dict) else None
        snapshots_by_id: dict[int, EpisodicSnapshot] = {}

        # 1) Semantic vector search
        try:
            semantic_results = await self._episodic.query_by_semantic(
                query_text=user_input,
                limit=5,
                session_id=sid,
            )
            for snap in semantic_results:
                snapshots_by_id[snap.id] = snap
        except Exception:
            logger.debug("Semantic search unavailable, continuing without past interactions")
            pass

        # 2) Temporal reference signals
        temporal_signals = [
            "上次", "昨天", "之前", "上次那个", "上次那首",
            "last time", "yesterday", "before", "last song", "previous",
            "again", "再", "又",
        ]
        if any(sig in user_input for sig in temporal_signals):
            recent = await self._episodic.query_recent(limit=3, session_id=sid)
            for snap in recent:
                snapshots_by_id[snap.id] = snap

        if not snapshots_by_id:
            return None

        sorted_snaps = sorted(
            snapshots_by_id.values(), key=lambda s: s.timestamp, reverse=True
        )

        lines = ["[Past interactions — you may reference these naturally]"]
        for snap in sorted_snaps[:5]:
            song_info = ""
            if snap.played_song_name:
                song_info = (
                    f" [played: {snap.played_song_artist or ''} - {snap.played_song_name}]"
                )
            mood_info = f" (mood: {snap.mood_tag})" if snap.mood_tag else ""
            sim_info = ""
            if snap.similarity_score is not None:
                sim_info = f" [similarity: {snap.similarity_score:.2f}]"
            lines.append(
                f"- User: {snap.user_input} | Aud.IO: {snap.assistant_reply[:120]}"
                f"{song_info}{mood_info}{sim_info}"
            )
        return "\n".join(lines)


class SessionSummaryProvider(ContextProvider):
    """跨会话记忆提供者 —— 注入本会话之前的 Reflection 摘要。

    v5 Reflection: 每 N 轮把短时对话压成摘要入库，下次会话启动时
    注入这里，DJ 跨会话不失忆（"上次你说想学吉他来着"）。
    """

    name = "session_summaries"

    def __init__(self, episodic: EpisodicMemory, limit: int = 3) -> None:
        self._episodic = episodic
        self._limit = limit

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        sid = metadata.get("_session_id") if isinstance(metadata, dict) else None
        if not sid:
            return None
        try:
            summaries = await self._episodic.query_recent_summaries(sid, limit=self._limit)
        except Exception:
            return None
        if not summaries:
            return None

        lines = ["[Previous sessions — what we've talked about (use naturally, don't announce it)]"]
        for item in reversed(summaries):  # 旧→新
            text = str(item.get("summary_text") or "").strip()
            if not text:
                continue
            topics = item.get("topics") or []
            topic_str = f" (topics: {', '.join(topics)})" if topics else ""
            lines.append(f"- {text}{topic_str}")
        if len(lines) == 1:
            return None
        return "\n".join(lines)


# ============================================================
# Assembler (RFC-007: user-prompt only — system prompt is caller's concern)
# ============================================================

class ContextAssembler:
    """Assembles the user-prompt portion: context blocks + user input.

    System-level prompts (identity, task, output schema) are NOT assembled here.
    Callers obtain them from prompts.py and send them as role="system".
    """

    def __init__(self, providers: list[ContextProvider]) -> None:
        self.providers = providers

    async def assemble(
        self,
        user_input: str,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
        resolved_song: dict[str, Any] | None = None,
        tool_constraints: str = "",
        session_id: str | None = None,
    ) -> str:
        """Build the user-prompt: context blocks + optional extras + user input.

        Args:
            user_input: The user's raw message.
            intent: Classified intent (gates which providers activate).
            metadata: Frontend context (Currently Playing, raw_context, etc.).
            resolved_song: (RFC-003) Real song data from Phase 1 pre-fetch.
            tool_constraints: Optional tool usage rules block (used by single-pass).
            session_id: Current session for per-session memory isolation.

        Returns:
            The user-prompt string to send as role="user".
        """
        meta = dict(metadata or {})
        if session_id:
            meta["_session_id"] = session_id
        context_blocks: list[str] = []

        for provider in self.providers:
            try:
                block = await provider.get_context(intent, user_input, meta)
                if block:
                    context_blocks.append(block)
            except Exception:
                logger.warning(
                    "Provider '%s' failed, skipping: ", type(provider).__name__,
                    exc_info=True,
                )
                continue

        context_text = "\n\n".join(context_blocks) if context_blocks else "- none"

        raw_context = meta.get("raw_context", {})
        raw_lines = [f"- {key}: {_context_to_text(value)}" for key, value in raw_context.items()]
        raw_block = "\n".join(raw_lines) if raw_lines else ""

        sections: list[str] = []

        if resolved_song:
            sections.append(format_resolved_song(resolved_song))

        if tool_constraints:
            sections.append(tool_constraints)

        if raw_block:
            sections.append(f"Additional Context:\n{raw_block}")

        sections.append(f"Context:\n{context_text}")
        sections.append(f"User:\n{user_input}")

        return "\n\n".join(sections)


def _context_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
