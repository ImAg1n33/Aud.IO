from typing import Any


def search_song(keyword: str) -> dict[str, Any]:
    """Stub for NetEase Cloud Music search."""
    return {
        "provider": "netease",
        "keyword": keyword,
        "results": [],
        "message": "Implement real NetEase API integration here.",
    }
