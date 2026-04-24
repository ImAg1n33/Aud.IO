import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT_SECONDS = 15


def _get_base_url() -> str:
    configured = os.getenv("NETEASE_API_BASE_URL", "").strip().rstrip("/")
    return configured or DEFAULT_BASE_URL


def _get_cookie() -> str:
    return os.getenv("NETEASE_COOKIE", "").strip()


def _request_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    request_params = {k: v for k, v in params.items() if v is not None}
    cookie = _get_cookie()
    if cookie:
        request_params["cookie"] = cookie

    query = urlencode(request_params)
    url = f"{_get_base_url()}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Aud.IO/0.1 (+https://github.com)",
    }
    if cookie:
        headers["Cookie"] = cookie

    req = Request(
        url,
        headers=headers,
        method="GET",
    )

    with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("NetEase API returned non-object JSON payload.")
    return payload


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


def search_first_song(keyword: str) -> dict[str, Any]:
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
            payload = _request_json(path, params)
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
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        raise RuntimeError("Failed to search song. " + " | ".join(errors))
    raise LookupError(f"No matched songs found for keyword: {term}")


def search_song(keyword: str) -> dict[str, Any]:
    """Backward-compatible alias for first-song search."""
    return search_first_song(keyword)


def get_song_mp3_url(song_id: str, level: str = "standard") -> str:
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
            payload = _request_json(path, params)
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
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")

    if errors:
        raise RuntimeError("Failed to fetch song url. " + " | ".join(errors))
    raise LookupError(f"No playable url found for song_id: {sid}")
