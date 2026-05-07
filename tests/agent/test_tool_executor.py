import pytest

from backend.agent.tool_executor import ToolExecutor
from backend.tools.base import (
    BaseTool,
    MusicCopyrightError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolResult,
    tool_registry,
)


class FakeSuccessTool(BaseTool):
    name = "fake_success"
    description = "Always succeeds."
    parameters = {}

    async def execute(self, **kwargs):
        return ToolResult.ok({"echo": kwargs})


class FakeFailTool(BaseTool):
    name = "fake_fail"
    description = "Always fails."
    parameters = {}

    async def execute(self, **kwargs):
        return ToolResult.fail(ToolExecutionError("always fails"))


class FakeCopyrightFirstThenOkTool(BaseTool):
    name = "fake_copyright"
    description = "Fails with copyright once, then succeeds."
    parameters = {}

    def __init__(self):
        super().__init__()
        self.call_count = 0

    async def execute(self, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return ToolResult.fail(
                MusicCopyrightError("blocked"),
                data={"name": "Famous Song", "requested_keyword": "famous"},
            )
        return ToolResult.ok({"name": "Lesser Known Song", "artist": "Indie Artist"})


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor(max_retries=2)


@pytest.fixture(autouse=True)
def _setup_registry() -> None:
    tool_registry.reset()
    tool_registry.register(FakeSuccessTool())
    tool_registry.register(FakeFailTool())
    yield
    tool_registry.reset()


class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_success(self, executor) -> None:
        result = await executor.execute_action({"tool": "fake_success", "key": "val"})
        assert result.success is True
        assert result.data["echo"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_failure(self, executor) -> None:
        result = await executor.execute_action({"tool": "fake_fail"})
        assert result.success is False
        assert isinstance(result.error, ToolExecutionError)

    @pytest.mark.asyncio
    async def test_tool_not_found(self, executor) -> None:
        result = await executor.execute_action({"tool": "ghost"})
        assert result.success is False
        assert isinstance(result.error, ToolNotFoundError)

    @pytest.mark.asyncio
    async def test_missing_tool_field(self, executor) -> None:
        result = await executor.execute_action({"keyword": "hello"})
        assert result.success is False
        assert isinstance(result.error, ToolExecutionError)


class TestExecuteActions:
    @pytest.mark.asyncio
    async def test_sequential_success(self, executor) -> None:
        actions = [
            {"tool": "fake_success", "a": 1},
            {"tool": "fake_success", "b": 2},
        ]
        results = await executor.execute_actions(actions)
        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_retry_context_on_copyright(self, executor) -> None:
        tool_registry.register(FakeCopyrightFirstThenOkTool())
        actions = [{"tool": "fake_copyright"}]
        results = await executor.execute_actions(actions)
        assert len(results) == 1
        assert "retry_context" in results[0].metadata

    @pytest.mark.asyncio
    async def test_retry_signal_when_copyright(self, executor) -> None:
        tool_registry.register(FakeCopyrightFirstThenOkTool())
        result = await executor.execute_action({"tool": "fake_copyright"})
        assert result.success is False
        assert executor._should_retry(result, attempt=0) is True
        assert executor._should_retry(result, attempt=2) is False


class TestRetryContext:
    def test_builds_from_song_name(self, executor) -> None:
        failed = ToolResult.fail(
            MusicCopyrightError("blocked"),
            data={"name": "Famous Song"},
        )
        ctx = executor._build_retry_context(failed)
        assert "Famous Song" in ctx
        assert "copyright" in ctx

    def test_builds_from_keyword_fallback(self, executor) -> None:
        failed = ToolResult.fail(
            MusicCopyrightError("blocked"),
            data={"requested_keyword": "some search"},
        )
        ctx = executor._build_retry_context(failed)
        assert "some search" in ctx
