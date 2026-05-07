"""Weather tool — currently stub, ready for real API integration."""

import os
from typing import Any

from backend.tools.base import BaseTool, ToolResult, WeatherError, tool_registry


class WeatherTool(BaseTool):
    name = "get_weather"
    description = (
        "Get current weather conditions for a city. "
        "Returns temperature, condition (sunny/rainy/cloudy/snowy), and a summary. "
        "Use when the user asks about weather or when weather might influence music recommendations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name in Chinese or English.",
            }
        },
        "required": ["city"],
    }

    def is_available(self) -> bool:
        return bool(os.getenv("WEATHER_API_KEY", "").strip())

    async def execute(self, **kwargs: Any) -> ToolResult:
        city = str(kwargs.get("city", "")).strip() or "unknown"
        api_key = os.getenv("WEATHER_API_KEY", "").strip()

        if not api_key:
            return ToolResult.ok(
                {
                    "city": city,
                    "temperature": None,
                    "condition": "unknown",
                    "summary": f"Weather data not available for {city} (API key not configured).",
                },
                provider="stub",
            )

        # TODO: Integrate real weather API (OpenWeatherMap / HeFeng)
        try:
            return ToolResult.ok(
                {
                    "city": city,
                    "temperature": None,
                    "condition": "unknown",
                    "summary": "Real weather API integration pending.",
                },
                provider="stub",
            )
        except Exception as exc:
            return ToolResult.fail(WeatherError(str(exc)), data={"city": city})


# Backward-compatible stub function
def get_weather(city: str) -> dict[str, Any]:
    """Stub for weather lookup — kept for backward compatibility."""
    return {
        "city": city,
        "temperature": None,
        "condition": "unknown",
        "message": "Implement real weather API integration here.",
    }


tool_registry.register(WeatherTool())
