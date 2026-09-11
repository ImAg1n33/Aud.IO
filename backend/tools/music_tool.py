"""NetEase Cloud Music tools implementing the BaseTool protocol."""

from typing import Any

from backend.config import settings
from backend.tools.base import (
    BaseTool,
    MusicCopyrightError,
    MusicSearchError,
    MusicUnavailableError,
    ToolExecutionError,
    ToolResult,
    tool_registry,
)
from backend.tools.netease_api import (
    CookieExpiredError,
    NoAcceptableSongError,
    find_similar_songs,
    get_song_mp3_url,
    search_first_song,
)


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
        return bool(settings.netease_cookie.strip())

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
                    "cover_url": song.get("cover_url", ""),
                    "album": song.get("album", ""),
                    "duration_ms": song.get("duration_ms"),
                    "requested_keyword": keyword,
                },
                provider="netease",
            )
        except CookieExpiredError as exc:
            return ToolResult.fail(
                MusicSearchError(str(exc)),
                data={"requested_keyword": keyword},
            )
        except NoAcceptableSongError as exc:
            # 有结果但全是翻唱/劣质版本 —— 可重试换曲（v0.7）
            return ToolResult.fail(
                MusicUnavailableError(str(exc)),
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
        return bool(settings.netease_cookie.strip())

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


class FindSimilarTool(BaseTool):
    """相似歌曲工具 —— 真实音乐关系数据（/simi/song），非 LLM 猜测。"""

    name = "find_similar"
    category = "music"
    description = (
        "Find a real similar song from the currently playing song id "
        "(NetEase music-relationship data). mode='similar' returns the closest "
        "match; mode='explore' returns a nearby but different pick. Use when "
        "the user asks for something similar to what's playing, or explicitly "
        "wants something different but adjacent ('来点不一样的')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "song_id": {
                "type": "string",
                "description": "Current song id (from [Currently Playing]).",
            },
            "mode": {
                "type": "string",
                "enum": ["similar", "explore"],
                "description": "similar = closest match; explore = nearby but different.",
            },
        },
        "required": ["song_id"],
    }

    def is_available(self) -> bool:
        return bool(settings.netease_cookie.strip())

    async def execute(self, **kwargs: Any) -> ToolResult:
        song_id = str(kwargs.get("song_id", "")).strip()
        mode = str(kwargs.get("mode", "similar")).strip().lower()
        if not song_id:
            return ToolResult.fail(
                ToolExecutionError("song_id is required for find_similar"),
            )

        try:
            candidates = await find_similar_songs(song_id, limit=10)
        except CookieExpiredError as exc:
            return ToolResult.fail(MusicSearchError(str(exc)))
        except Exception as exc:
            return ToolResult.fail(MusicSearchError(str(exc)))

        if not candidates:
            return ToolResult.fail(
                MusicUnavailableError("no acceptable similar songs found"),
                data={"requested_keyword": f"similar:{song_id}"},
            )

        # similar → 最热（最相近）；explore → 次热（相近但不同）
        index = 0 if mode != "explore" else min(1, len(candidates) - 1)
        pick = candidates[index]
        return ToolResult.ok(
            {
                "song_id": pick["id"],
                "name": pick["name"],
                "artist": pick["artist"],
                "cover_url": pick.get("cover_url", ""),
                "album": pick.get("album", ""),
                "duration_ms": pick.get("duration_ms"),
                "requested_keyword": f"similar:{mode}",
            },
            provider="netease",
        )


tool_registry.register(SearchMusicTool())
tool_registry.register(GetMusicUrlTool())
tool_registry.register(FindSimilarTool())
