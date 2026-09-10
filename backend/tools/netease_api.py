"""NetEase Cloud Music API — unified httpx async backend.

v0.3: urllib → httpx + Cookie expiry detection + transient retry.
"""

import json
import logging
import re
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2

# 搜索候选数（v0.7 挑歌精度：评分选优而非取第一条）
_CANDIDATE_LIMIT = 15

# 劣质版本标记（翻唱/改编/伴奏类；Live/Remix 可能是正规版本，不列入）
_LOW_QUALITY_MARKERS = (
    "原唱", "翻唱", "cover", "女声版", "男声版", "钢琴版", "吉他版",
    "纯音乐", "伴奏", "dj版", "beat", "深情版", "治愈版", "正式版",
)


class NetEaseError(RuntimeError):
    """Base for all NetEase API errors."""


class CookieExpiredError(NetEaseError):
    """NetEase cookie is expired or missing — re-login required."""


class NoAcceptableSongError(NetEaseError):
    """搜索有结果但全部为翻唱/劣质版本 —— 可重试换曲（不静默播翻唱）。"""


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
# 搜索质量评分（v0.7 挑歌精度）
# ================================================================


def _score_candidate(song: dict[str, Any], keyword: str) -> float:
    """确定性候选评分 —— 翻唱惩罚 / 原唱加成 / 艺人匹配 / 标题匹配 / 热度。

    实证依据（v0.7 RFC-019）：
    - 翻唱全部标 originCoverType=2、原唱=1（cloudsearch 提供）
    - 假名账号（如 "周杰伦." 带点）不应被艺人匹配命中 —— 子串匹配天然防伪
    - 周杰伦目录在网易云无官方版：宁可换曲，不静默播翻唱

    Returns:
        分数；<= 0 视为不可接受（翻唱/劣质/不匹配）
    """
    name = str(song.get("name") or "")
    lowered_name = name.lower()
    lowered_kw = keyword.strip().lower()

    score = 0.0

    # 1) 原唱/翻唱信号（最强，cloudsearch 提供）
    cover_type = song.get("originCoverType")
    if cover_type == 2:
        score -= 3.0        # 翻唱
    elif cover_type == 1:
        score += 1.5        # 原唱
    elif cover_type == 3:
        score -= 1.0        # 伴奏/DJ/改编类

    # 2) 标题劣质标记（兜底 + 强化）
    if any(marker in lowered_name for marker in _LOW_QUALITY_MARKERS):
        score -= 3.0

    # 3) 艺人匹配：候选艺人名（整体）出现在关键词中
    artists = [
        a.strip().lower()
        for a in _extract_artist_name(song).split(",")
        if a.strip()
    ]
    if any(a and a in lowered_kw for a in artists):
        score += 2.0

    # 4) 标题匹配（去掉括号注释后）
    base_title = re.sub(r"[（(\[].*?[)）\]]", "", name).strip().lower()
    if base_title and (base_title in lowered_kw or lowered_kw in base_title):
        score += 1.0

    # 5) 热度加成（cloudsearch 的 pop，0-100）
    pop = song.get("pop")
    if isinstance(pop, (int, float)) and pop > 0:
        score += min(float(pop), 100.0) / 100.0

    # 6) 可播性提示：无 VIP 时 pl=0 且付费 → 小惩罚（软信号，不硬过滤）
    priv = song.get("privilege")
    if isinstance(priv, dict) and priv.get("pl") == 0 and (song.get("fee") or 0) > 0:
        score -= 0.5

    return score


def _is_unavailable(song: dict[str, Any]) -> bool:
    """不可用版本（status/st < 0）。"""
    status = song.get("status")
    st = song.get("st")
    return (isinstance(status, int) and status < 0) or (
        isinstance(st, int) and st < 0
    )


# ================================================================
# Public API
# ================================================================


async def search_first_song(keyword: str) -> dict[str, Any]:
    """Search and pick the best-scored candidate (v0.7 挑歌精度).

    拉取候选集（cloudsearch 优先，含 originCoverType/pop 等信号）→ 确定性评分
    （翻唱惩罚/原唱加成/艺人匹配/热度）→ 取最优。
    全劣质候选 → NoAcceptableSongError（走容错链换曲，不静默播翻唱）。

    Returns:
        {"id": str, "name": str, "artist": str}
    """
    term = keyword.strip()
    if not term:
        raise ValueError("keyword cannot be empty.")

    attempts = [
        ("cloudsearch", {"keywords": term, "limit": _CANDIDATE_LIMIT, "type": 1}),
        ("search", {"keywords": term, "limit": _CANDIDATE_LIMIT, "type": 1}),
    ]

    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    for path, params in attempts:
        try:
            payload = await _request_json(path, params)
            songs = _extract_song_list(payload)
            if songs:
                candidates = songs
                break
        except CookieExpiredError:
            raise
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    if not candidates:
        if errors:
            raise RuntimeError("Failed to search song. " + " | ".join(errors))
        raise LookupError(f"No matched songs found for keyword: {term}")

    best: dict[str, Any] | None = None
    best_score = float("-inf")
    for song in candidates:
        if _is_unavailable(song):
            continue
        song_id = song.get("id")
        name = song.get("name")
        if song_id is None or not isinstance(name, str) or not name.strip():
            continue
        score = _score_candidate(song, term)
        if score > best_score:
            best = song
            best_score = score

    if best is None or best_score <= 0:
        raise NoAcceptableSongError(
            f"No acceptable song for '{term}' "
            f"(best_score={best_score if best_score != float('-inf') else 'n/a'}) — "
            "candidates are covers / low-quality versions."
        )

    return {
        "id": str(best.get("id")),
        "name": str(best.get("name")).strip(),
        "artist": _extract_artist_name(best),
    }


async def search_song(keyword: str) -> dict[str, Any]:
    """Backward-compatible alias for first-song search."""
    return await search_first_song(keyword)


async def find_similar_songs(song_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """相似歌曲（NetEase /simi/song）—— 真实音乐关系数据（v0.7 P1-1）。

    过滤：不可用版本 / 翻唱（originCoverType=2）/ 劣质标记；按热度降序。

    Returns:
        [{"id", "name", "artist", "popularity"}]
    """
    sid = str(song_id).strip()
    if not sid:
        raise ValueError("song_id cannot be empty.")

    payload = await _request_json("simi/song", {"id": sid})
    songs = payload.get("songs")
    if not isinstance(songs, list):
        return []

    result: list[dict[str, Any]] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        if _is_unavailable(song):
            continue
        raw_id = song.get("id")
        name = song.get("name")
        if raw_id is None or str(raw_id) == sid:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        if any(marker in name.lower() for marker in _LOW_QUALITY_MARKERS):
            continue
        if song.get("originCoverType") == 2:
            continue

        pop = song.get("popularity")
        result.append(
            {
                "id": str(raw_id),
                "name": name.strip(),
                "artist": _extract_artist_name(song),
                "popularity": int(pop) if isinstance(pop, (int, float)) else 0,
            }
        )

    result.sort(key=lambda x: x["popularity"], reverse=True)
    return result[:limit]


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
