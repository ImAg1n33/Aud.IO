import pytest

from backend.tools.base import (
    BaseTool,
    MusicCopyrightError,
    MusicSearchError,
    ToolConfigError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    tool_registry,
)


# ============================================================
# ToolResult
# ============================================================

def test_tool_result_success() -> None:
    result = ToolResult.ok(data={"song_id": "123"}, latency=0.5)
    assert result.success is True
    assert result.data == {"song_id": "123"}
    assert result.error is None
    assert result.metadata == {"latency": 0.5}


def test_tool_result_failure() -> None:
    err = MusicCopyrightError("blocked")
    result = ToolResult.fail(err, data={"keyword": "x"}, attempt=1)
    assert result.success is False
    assert result.error is err
    assert result.data == {"keyword": "x"}
    assert result.metadata == {"attempt": 1}


# ============================================================
# Error hierarchy
# ============================================================

def test_tool_error_is_exception() -> None:
    with pytest.raises(ToolError):
        raise ToolError("base")


def test_error_inheritance() -> None:
    assert issubclass(MusicSearchError, ToolExecutionError)
    assert issubclass(MusicCopyrightError, ToolExecutionError)
    assert issubclass(ToolExecutionError, ToolError)
    assert issubclass(ToolConfigError, ToolError)


def test_tool_not_found_error() -> None:
    err = ToolNotFoundError("missing_tool not found")
    assert isinstance(err, ToolError)
    assert "missing_tool" in str(err)


# ============================================================
# Registry
# ============================================================

class FakeAvailableTool(BaseTool):
    name = "fake_available"
    description = "A tool that is available."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolResult.ok({"fake": True})


class FakeUnavailableTool(BaseTool):
    name = "fake_unavailable"
    description = "A tool that is not available."
    parameters = {"type": "object", "properties": {}}

    def is_available(self) -> bool:
        return False

    async def execute(self, **kwargs):
        return ToolResult.ok()


@pytest.fixture
def clean_registry() -> None:
    tool_registry.reset()
    yield
    tool_registry.reset()


def test_registry_register_and_get(clean_registry) -> None:
    tool = FakeAvailableTool()
    tool_registry.register(tool)
    assert tool_registry.get("fake_available") is tool
    assert "fake_available" in tool_registry
    assert len(tool_registry) == 1


def test_registry_get_missing_raises(clean_registry) -> None:
    with pytest.raises(ToolNotFoundError):
        tool_registry.get("ghost")


def test_registry_register_empty_name_raises(clean_registry) -> None:
    tool = FakeAvailableTool()
    tool.name = ""
    with pytest.raises(ValueError):
        tool_registry.register(tool)


def test_registry_get_all(clean_registry) -> None:
    tool_registry.register(FakeAvailableTool())
    tool_registry.register(FakeUnavailableTool())
    assert len(tool_registry.get_all()) == 2


def test_registry_get_available_filters(clean_registry) -> None:
    tool_registry.register(FakeAvailableTool())
    tool_registry.register(FakeUnavailableTool())
    available = tool_registry.get_available()
    assert len(available) == 1
    assert available[0].name == "fake_available"


def test_registry_get_schemas(clean_registry) -> None:
    tool_registry.register(FakeAvailableTool())
    schemas = tool_registry.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "fake_available"
    assert "description" in schemas[0]
    assert "parameters" in schemas[0]


def test_registry_reset(clean_registry) -> None:
    tool_registry.register(FakeAvailableTool())
    tool_registry.reset()
    assert len(tool_registry) == 0
    assert len(tool_registry.get_all()) == 0


# ============================================================
# to_json_schema
# ============================================================

def test_to_json_schema_structure() -> None:
    schema = FakeAvailableTool().to_json_schema()
    assert schema["name"] == "fake_available"
    assert schema["description"] == "A tool that is available."
    assert schema["parameters"] == {"type": "object", "properties": {}}
