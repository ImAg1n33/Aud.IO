"""NetEase Cloud Music API — unified httpx async backend.

v0.3: urllib → httpx + Cookie expiry detection + transient retry.
"""

import json
import logging
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2


class NetEaseError(RuntimeError):
    """Base for all NetEase API errors."""


class CookieExpiredError(NetEaseError):
    """NetEase cookie is expired or missing — re-login required."""


def _get_base_url() -> str:
    configured = settings.netease_api_base_url.strip().rstrip("/")
    return configured or DEFAULT_BASE_URL


def _get_cookie() -> str:
    return settings.netease_cookie.strip()


# ================================================================
# Core HTTP transport (unified httpx, transient retry)
# ================================================================


async def _request_json(
    path: str,
    params: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Async GET → JSON with transient retry and cookie-expiry detection."""
    request_params = {k: v for k, v in params.items() if v is not None}
    cookie = _get_cookie()
    if cookie:
        request_params["cookie"] = cookie

    url = f"{_get_base_url()}/{path.lstrip('/')}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Aud.IO/0.2 (+https://github.com)",
    }
    if cookie:
        headers["Cookie"] = cookie

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=request_params, headers=headers)

            # --- Cookie expiry detection (1.5) ---
            if response.status_code in (301, 401, 403):
                raise CookieExpiredError(
                    f"NetEase cookie appears expired (HTTP {response.status_code}). "
                    "Run 'python backend/tools/login_netease.py' to re-login."
                )

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise ValueError("NetEase API returned non-object JSON payload.")

            # Check NetEase-specific status codes
            code = payload.get("code")
            if isinstance(code, int) and code in (301, 800):
                raise CookieExpiredError(
                    f"NetEase API returned auth error (code={code}). "
                    "Run 'python backend/tools/login_netease.py' to re-login."
                )

            return payload

        except CookieExpiredError:
            raise  # Don't retry — cookie is dead

        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                logger.warning(
                    "NetEase API transient error (attempt %d/%d): %s",
                    attempt + 1, MAX_RETRIES + 1, exc,
                )
                continue

    raise NetEaseError(
        f"NetEase API request failed after {MAX_RETRIES + 1} attempts: {last_error}"
    )


# ================================================================
# Song helpers
# ================================================================


def _extract_song_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("songs"), list):
        return [item for item in result["songs"] if isinstance(item, dict)]

    songs = payload.get("songs")
    if isinstance(songs, list):
        return [item for item in songs if isinstance(item, dict)]

    return []


def _extract_artist_name(song: dict[str, Any]) -> str:
    artists = song.get("artists")
    if not isinstance(artists, list):
        artists = song.get("ar")

    if not isinstance(artists, list):
        return ""

    names = []
    for artist in artists:
        if isinstance(artist, dict):
            name = artist.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())

    return ", ".join(names)


# ================================================================
# Public API
# ================================================================


async def search_first_song(keyword: str) -> dict[str, Any]:
    """Search by keyword and return the first matched song.

    Returns:
        {"id": str, "name": str, "artist": str}
    """
    term = keyword.strip()
    if not term:
        raise ValueError("keyword cannot be empty.")

    attempts = [
        ("search", {"keywords": term, "limit": 1, "type": 1}),
        ("cloudsearch", {"keywords": term, "limit": 1, "type": 1}),
    ]

    errors: list[str] = []
    for path, params in attempts:
        try:
            payload = await _request_json(path, params)
            songs = _extract_song_list(payload)
            if not songs:
                continue

            first = songs[0]
            song_id = first.get("id")
            name = first.get("name")
            artist = _extract_artist_name(first)

            if song_id is None or not isinstance(name, str) or not name.strip():
                continue

            return {
                "id": str(song_id),
                "name": name.strip(),
                "artist": artist,
            }
        except CookieExpiredError:
            raise
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        raise RuntimeError("Failed to search song. " + " | ".join(errors))
    raise LookupError(f"No matched songs found for keyword: {term}")


async def search_song(keyword: str) -> dict[str, Any]:
    """Backward-compatible alias for first-song search."""
    return await search_first_song(keyword)


async def get_song_mp3_url(song_id: str, level: str = "standard") -> str:
    """Get MP3 playback URL by song ID.

    Args:
        song_id: NetEase song id
        level: quality level for /song/url/v1 endpoint
    """
    sid = str(song_id).strip()
    if not sid:
        raise ValueError("song_id cannot be empty.")

    attempts = [
        ("song/url/v1", {"id": sid, "level": level}),
        ("song/url", {"id": sid, "br": 320000}),
    ]

    errors: list[str] = []
    for path, params in attempts:
        try:
            payload = await _request_json(path, params)
            data = payload.get("data")

            candidate: dict[str, Any] | None = None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                candidate = data[0]
            elif isinstance(data, dict):
                candidate = data

            if not candidate:
                continue

            url = candidate.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
        except CookieExpiredError:
            raise
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        raise RuntimeError("Failed to fetch song url. " + " | ".join(errors))
    raise LookupError(f"No playable url found for song_id: {sid}")
