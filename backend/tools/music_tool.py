"""NetEase Cloud Music tools implementing the BaseTool protocol."""

import os
from typing import Any

from backend.tools.base import (
    BaseTool,
    MusicCopyrightError,
    MusicSearchError,
    ToolExecutionError,
    ToolResult,
    tool_registry,
)
from backend.tools.netease_api import CookieExpiredError, get_song_mp3_url, search_first_song


class SearchMusicTool(BaseTool):
    name = "search_music"
    category = "music"
    description = (
        "Search NetEase Cloud Music for a song by keyword. "
        "Returns the best match with song id, name, and artist. "
        "Use when the user asks to play or find specific music."
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Search keyword — must be 'Artist Name Song Title' or 'Song Title', never genre descriptions or pronouns.",
            }
        },
        "required": ["keyword"],
    }

    def is_available(self) -> bool:
        return bool(os.getenv("NETEASE_COOKIE", "").strip())

    async def execute(self, **kwargs: Any) -> ToolResult:
        keyword = str(kwargs.get("keyword", "")).strip()
        if not keyword:
            return ToolResult.fail(
                ToolExecutionError("keyword is required for search_music"),
            )

        try:
            song = await search_first_song(keyword)
            return ToolResult.ok(
                {
                    "song_id": song["id"],
                    "name": song["name"],
                    "artist": song["artist"],
                    "requested_keyword": keyword,
                },
                provider="netease",
            )
        except CookieExpiredError as exc:
            return ToolResult.fail(
                MusicSearchError(str(exc)),
                data={"requested_keyword": keyword},
            )
        except Exception as exc:
            return ToolResult.fail(
                MusicSearchError(str(exc)),
                data={"requested_keyword": keyword},
            )


class GetMusicUrlTool(BaseTool):
    name = "get_music_url"
    category = "music"
    description = (
        "Get a playable MP3 URL for a song by its NetEase song ID. "
        "Returns the direct audio URL. May fail with copyright restrictions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "song_id": {
                "type": "string",
                "description": "NetEase song ID (numeric string).",
            }
        },
        "required": ["song_id"],
    }

    def is_available(self) -> bool:
        return bool(os.getenv("NETEASE_COOKIE", "").strip())

    async def execute(self, **kwargs: Any) -> ToolResult:
        song_id = str(kwargs.get("song_id", "")).strip()
        if not song_id:
            return ToolResult.fail(
                ToolExecutionError("song_id is required for get_music_url"),
            )

        try:
            mp3_url = await get_song_mp3_url(song_id)
            return ToolResult.ok({"mp3_url": mp3_url, "song_id": song_id}, provider="netease")
        except LookupError as exc:
            return ToolResult.fail(
                MusicCopyrightError(str(exc)),
                data={"song_id": song_id},
            )
        except CookieExpiredError as exc:
            return ToolResult.fail(
                ToolExecutionError(str(exc)),
                data={"song_id": song_id},
            )
        except Exception as exc:
            return ToolResult.fail(
                ToolExecutionError(str(exc)),
                data={"song_id": song_id},
            )


tool_registry.register(SearchMusicTool())
tool_registry.register(GetMusicUrlTool())
