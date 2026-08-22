"""Tool protocol: abstract base, result types, error hierarchy, and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ============================================================
# Error hierarchy
# ============================================================

class ToolError(Exception):
    """Base for all tool failures."""


class ToolNotFoundError(ToolError):
    """Tool name not found in registry."""


class ToolConfigError(ToolError):
    """Tool is misconfigured (missing keys, invalid settings)."""


class ToolExecutionError(ToolError):
    """Runtime failure during tool execution."""


class MusicSearchError(ToolExecutionError):
    """NetEase search API failure."""


class MusicCopyrightError(ToolExecutionError):
    """Song is blocked by copyright — retry signal."""


class WeatherError(ToolExecutionError):
    """Weather API failure."""


class TTSError(ToolExecutionError):
    """TTS generation failure."""


# ============================================================
# Result type
# ============================================================

@dataclass
class ToolResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: ToolError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None, **metadata: Any) -> "ToolResult":
        return cls(success=True, data=data or {}, metadata=metadata)

    @classmethod
    def fail(cls, error: ToolError, data: dict[str, Any] | None = None, **metadata: Any) -> "ToolResult":
        return cls(success=False, data=data or {}, error=error, metadata=metadata)


# ============================================================
# Abstract tool
# ============================================================

class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    # 工具类别（music/weather/tts/general）—— 意图门控决定哪些工具对 LLM 可见
    category: str = "general"

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_openai_function_schema(self) -> dict[str, Any]:
        """OpenAI 原生 function calling 格式（RFC: function calling 重构）。"""
        return {"type": "function", "function": self.to_json_schema()}

    def is_available(self) -> bool:
        """Override to check config/env requirements."""
        return True


# ============================================================
# Registry
# ============================================================

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        return tool

    def get_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_available(self) -> list[BaseTool]:
        return [t for t in self._tools.values() if t.is_available()]

    def get_schemas(self) -> list[dict[str, Any]]:
        return [t.to_json_schema() for t in self.get_available()]

    def reset(self) -> None:
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


tool_registry = ToolRegistry()
