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
from backend.tools.mcp_adapter import (
    MCPClientManager,
    MCPToolAdapter,
    register_mcp_tools,
)
from backend.tools.netease_api import (
    CookieExpiredError,
    NetEaseError,
    get_song_mp3_url,
    search_first_song,
    search_song,
)

# Import tool modules to trigger registry auto-registration
import backend.tools.music_tool  # noqa: E402, F401 — registers SearchMusicTool, GetMusicUrlTool

# MCP adapters are registered at app startup via main.py → register_mcp_tools()

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
    # MCP layer
    "MCPToolAdapter",
    "MCPClientManager",
    "register_mcp_tools",
    # NetEase
    "CookieExpiredError",
    "NetEaseError",
    "search_song",
    "search_first_song",
    "get_song_mp3_url",
]
