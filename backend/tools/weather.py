from typing import Any


def get_weather(city: str) -> dict[str, Any]:
    """Stub for weather lookup tool."""
    return {
        "city": city,
        "temperature": None,
        "condition": "unknown",
        "message": "Implement real weather API integration here.",
    }
