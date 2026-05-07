"""Tool dispatch engine with retry and graceful degradation."""

import logging
from typing import Any

from backend.tools.base import (
    MusicCopyrightError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolResult,
    tool_registry,
)

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (MusicCopyrightError,)


class ToolExecutor:
    def __init__(self, max_retries: int = 2) -> None:
        self.max_retries = max_retries
        self._registry = tool_registry

    async def execute_action(self, action: dict[str, Any]) -> ToolResult:
        """Execute a single action dict from the LLM output.

        Action format: {"tool": "<name>", ...params}
        Returns ToolResult (never raises).
        """
        tool_name = str(action.get("tool", "")).strip()
        if not tool_name:
            return ToolResult.fail(
                ToolExecutionError("Action missing 'tool' field."),
                data={"action": action},
            )

        try:
            tool = self._registry.get(tool_name)
        except ToolNotFoundError as exc:
            return ToolResult.fail(exc, data={"action": action})

        params = {k: v for k, v in action.items() if k != "tool"}

        try:
            return await tool.execute(**params)
        except Exception as exc:
            logger.warning("Tool '%s' raised unhandled exception: %s", tool_name, exc)
            return ToolResult.fail(
                ToolExecutionError(str(exc)),
                data={"tool": tool_name, "params": params},
            )

    async def execute_actions(
        self, actions: list[dict[str, Any]]
    ) -> list[ToolResult]:
        """Execute a sequence of actions in order.

        On retryable errors, subsequent actions of the same type are retried.
        Non-retryable errors are returned as-is and execution continues.
        """
        results: list[ToolResult] = []
        retry_state: dict[str, int] = {}

        for action in actions:
            tool_name = str(action.get("tool", "")).strip()
            attempt = retry_state.get(tool_name, 0)
            result = await self.execute_action(action)

            if not result.success and self._should_retry(result, attempt):
                retry_context = self._build_retry_context(result)
                result.metadata["retry_context"] = retry_context
                retry_state[tool_name] = attempt + 1

            results.append(result)

        return results

    def _should_retry(self, result: ToolResult, attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        return isinstance(result.error, RETRYABLE_ERRORS)

    def _build_retry_context(self, failed_result: ToolResult) -> str:
        data = failed_result.data
        song_name = data.get("name") or data.get("requested_keyword")
        target = song_name if isinstance(song_name, str) and song_name.strip() else "the requested song"
        return (
            f"The song '{target}' is blocked by copyright and cannot be played. "
            "Please choose a different song — a different artist or a less mainstream track "
            "that is more likely to have a free/playable version available."
        )
