"""Text-to-speech tool — currently stub, ready for real TTS API integration."""

import os
from typing import Any

from backend.tools.base import BaseTool, ToolResult, TTSError, tool_registry


class TTSTool(BaseTool):
    name = "synthesize_speech"
    description = (
        "Convert text to spoken audio. Returns audio bytes. "
        "Use when the user asks Aud.IO to speak something out loud."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to convert to speech.",
            },
            "voice": {
                "type": "string",
                "description": "Voice preset name.",
                "default": "default",
            },
        },
        "required": ["text"],
    }

    def is_available(self) -> bool:
        return bool(os.getenv("TTS_API_KEY", "").strip())

    async def execute(self, **kwargs: Any) -> ToolResult:
        text = str(kwargs.get("text", ""))
        voice = str(kwargs.get("voice", "default"))
        api_key = os.getenv("TTS_API_KEY", "").strip()

        if not api_key:
            return ToolResult.ok(
                {"audio": None, "format": None, "text": text},
                provider="stub",
                warning="TTS_API_KEY not configured.",
            )

        # TODO: Integrate real TTS API (Fish Audio / Azure / etc.)
        try:
            return ToolResult.ok(
                {"audio": None, "format": None, "text": text},
                provider="stub",
                warning="Real TTS API integration pending.",
            )
        except Exception as exc:
            return ToolResult.fail(TTSError(str(exc)), data={"text": text})


# Backward-compatible stub function
def synthesize_speech(text: str, voice: str = "default") -> bytes:
    """Stub for text-to-speech generation — kept for backward compatibility."""
    _ = text, voice
    return b""


tool_registry.register(TTSTool())
