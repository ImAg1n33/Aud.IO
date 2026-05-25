from backend.tools.base import (
    BaseTool,
    MusicCopyrightError,
    MusicSearchError,
    ToolConfigError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    TTSError,
    WeatherError,
    tool_registry,
)

# Import tool modules to trigger registry auto-registration
import backend.tools.music_tool  # noqa: E402, F401 — registers SearchMusicTool, GetMusicUrlTool
import backend.tools.tts        # noqa: E402, F401 — registers TTSTool
import backend.tools.weather    # noqa: E402, F401 — registers WeatherTool

# Backward-compatible exports (existing code depends on these)
from backend.tools.netease_api import CookieExpiredError, NetEaseError, get_song_mp3_url, search_first_song, search_song  # noqa: E402
from backend.tools.tts import synthesize_speech  # noqa: E402
from backend.tools.weather import get_weather  # noqa: E402

__all__ = [
    # Tool protocol
    "BaseTool",
    "ToolRegistry",
    "ToolResult",
    "tool_registry",
    # Errors
    "ToolError",
    "ToolNotFoundError",
    "ToolConfigError",
    "ToolExecutionError",
    "MusicSearchError",
    "MusicCopyrightError",
    "WeatherError",
    "TTSError",
    # Legacy functions (backward compatible)
    "search_song",
    "search_first_song",
    "get_song_mp3_url",
    "get_weather",
    "synthesize_speech",
]
