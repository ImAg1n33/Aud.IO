"""TTS provider — text-to-speech synthesis via ToolRegistry.

v0.4 (RFC-011): TTS is an enhancement, not a dependency.  Music playback
speed is the highest priority — TTS must never delay it.
"""

import asyncio
import logging
import re
from typing import Any

from backend.config import settings
from backend.tools.base import ToolNotFoundError, tool_registry

logger = logging.getLogger(__name__)

_SENTENCE_RE = re.compile(r"[。！？.!?\n]+")
_COMMA_RE = re.compile(r"[，,]+")
_TTS_TIMEOUT = 3.0  # per-segment synthesis timeout


class TTSProvider:
    """Synthesize text into speech audio URLs via a registered TTS tool.

    Looks up the tool by name in ToolRegistry — no direct MCP dependency.
    Every failure path returns an empty list so callers never need try/except.
    """

    def __init__(self, tool_name: str | None = None) -> None:
        self._tool_name = tool_name or settings.tts_tool_name.strip()

    # ── feature gates ──────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return settings.tts_enabled

    def intent_enabled(self, intent: str) -> bool:
        allowed = {x.strip() for x in settings.tts_intents.split(",") if x.strip()}
        return intent in allowed

    # ── public API ──────────────────────────────────────────────────

    def pre_roll_text(self, text: str, max_len: int = 80) -> str | None:
        """Extract the first sentence for use as a short DJ pre-roll tag.

        Returns None if the text is too short to be worth synthesising
        (< 10 chars) or if TTS is disabled.
        """
        if not self.is_enabled:
            return None
        text = (text or "").strip()
        if len(text) < 10:
            return None
        first = self._segment(text, max_len)[0] if self._segment(text, max_len) else ""
        return first if len(first) >= 10 else None

    async def synthesize(self, text: str, max_len: int = 200) -> list[str]:
        """Segment *text* and synthesise each segment.  Returns a list of
        audio URLs (may be empty).  Never raises — failures are logged and
        skipped.
        """
        if not self.is_enabled:
            return []
        if not text or not text.strip():
            return []

        segments = self._segment(text, max_len)
        urls: list[str] = []

        for seg in segments:
            try:
                url = await self._call_tts_tool(seg)
                if url:
                    urls.append(url)
            except Exception:
                logger.warning("TTS segment failed: %.60s...", seg, exc_info=True)

        return urls

    # ── segmentation ────────────────────────────────────────────────

    def _segment(self, text: str, max_len: int = 200) -> list[str]:
        """Split text by sentence delimiters, keeping whole sentences intact.

        Rules (priority order):
        1. Split on 。！？.!? and newlines
        2. If a single segment exceeds *max_len*, split further on ，
        3. If still over *max_len* with no delimiter, hard-cut at nearest space
        """
        text = text.strip()
        if not text:
            return []

        # Step 1 — sentence-level split
        parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
        if not parts:
            parts = [text]

        # Step 2 — split oversize parts
        result: list[str] = []
        for part in parts:
            if len(part) <= max_len:
                result.append(part)
            else:
                result.extend(self._split_long(part, max_len))
        return result

    def _split_long(self, text: str, max_len: int) -> list[str]:
        """Split a single over-length segment further."""
        # Try comma split first
        comma_parts = [p.strip() for p in _COMMA_RE.split(text) if p.strip()]
        if len(comma_parts) > 1:
            result: list[str] = []
            for cp in comma_parts:
                if len(cp) <= max_len:
                    result.append(cp)
                else:
                    result.extend(self._hard_cut(cp, max_len))
            return result

        return self._hard_cut(text, max_len)

    @staticmethod
    def _hard_cut(text: str, max_len: int) -> list[str]:
        """Cut *text* into *max_len*-sized chunks, breaking at spaces."""
        chunks: list[str] = []
        remaining = text
        while len(remaining) > max_len:
            cut = remaining[:max_len]
            # Try to break at last space
            last_space = cut.rfind(" ")
            if last_space > max_len // 2:
                cut = remaining[:last_space]
                remaining = remaining[last_space + 1:]
            else:
                remaining = remaining[max_len:]
            chunks.append(cut.strip())
        if remaining.strip():
            chunks.append(remaining.strip())
        return chunks

    # ── tool call ───────────────────────────────────────────────────

    async def _call_tts_tool(self, text: str) -> str | None:
        """Look up the TTS tool in ToolRegistry and call it.

        Returns the audio URL string, or None on any failure.
        """
        try:
            tool = tool_registry.get(self._tool_name)
        except ToolNotFoundError:
            logger.debug("TTS tool '%s' not registered", self._tool_name)
            return None

        try:
            result = await asyncio.wait_for(
                tool.execute(text=text),
                timeout=_TTS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("TTS tool '%s' timed out (%.1fs)", self._tool_name, _TTS_TIMEOUT)
            return None
        except Exception:
            logger.warning("TTS tool '%s' call failed", self._tool_name, exc_info=True)
            return None

        if not result.success:
            logger.debug("TTS tool returned failure: %s", result.error)
            return None

        data = result.data or {}
        return _extract_url(data)


def _extract_url(data: dict[str, Any]) -> str | None:
    """Extract an audio URL from a TTS tool result dict."""
    url = data.get("url") or data.get("audio_url") or data.get("mp3_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    # Some tools return a nested dict
    for key in ("result", "data", "audio"):
        inner = data.get(key)
        if isinstance(inner, dict):
            url = inner.get("url") or inner.get("audio_url") or inner.get("mp3_url")
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None
