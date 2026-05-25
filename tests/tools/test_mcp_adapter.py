"""Tests for MCP adapter layer — MCPToolAdapter + MCPClientManager + registration."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from backend.tools.base import ToolResult, tool_registry
from backend.tools.mcp_adapter import (
    MCPClientManager,
    MCPToolAdapter,
    _normalise_mcp_result,
    load_mcp_configs,
    register_mcp_tools,
)


class TestMCPToolAdapter:
    """MCPToolAdapter conforms to BaseTool interface."""

    @pytest.fixture
    def manager(self):
        mgr = Mock(spec=MCPClientManager)
        mgr.is_connected.return_value = True
        mgr.call_tool = AsyncMock(return_value={"text": "sunny, 22C", "is_error": False})
        return mgr

    @pytest.fixture
    def adapter(self, manager):
        return MCPToolAdapter(
            tool_schema={
                "name": "get_forecast",
                "description": "Get weather forecast for a city",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
            server_name="weather",
            manager=manager,
        )

    def test_name_description_parameters(self, adapter):
        assert adapter.name == "get_forecast"
        assert "weather" in adapter.description
        assert adapter.parameters["type"] == "object"
        assert "city" in adapter.parameters["properties"]

    def test_is_available_delegates_to_manager(self, adapter, manager):
        assert adapter.is_available() is True
        manager.is_connected.return_value = False
        assert adapter.is_available() is False

    @pytest.mark.asyncio
    async def test_execute_returns_tool_result_ok(self, adapter):
        result = await adapter.execute(city="Beijing")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data["text"] == "sunny, 22C"
        assert result.metadata["provider"] == "mcp"

    @pytest.mark.asyncio
    async def test_execute_returns_tool_result_fail(self, adapter, manager):
        manager.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        result = await adapter.execute(city="Beijing")
        assert result.success is False
        assert "boom" in str(result.error)


class TestLoadMCPConfigs:
    def test_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_SERVERS", raising=False)
        assert load_mcp_configs() == []

    def test_empty_json_array(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVERS", "[]")
        assert load_mcp_configs() == []

    def test_parses_valid_config(self, monkeypatch):
        cfg = json.dumps([
            {"name": "w", "transport": "stdio", "command": "npx", "args": ["-y", "x"]}
        ])
        monkeypatch.setenv("MCP_SERVERS", cfg)
        configs = load_mcp_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "w"

    def test_skips_disabled(self, monkeypatch):
        cfg = json.dumps([
            {"name": "w1", "command": "x", "enabled": False},
            {"name": "w2", "command": "y"},
        ])
        monkeypatch.setenv("MCP_SERVERS", cfg)
        configs = load_mcp_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "w2"

    def test_invalid_json_returns_empty(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVERS", "{not json}")
        assert load_mcp_configs() == []

    def test_non_array_returns_empty(self, monkeypatch):
        monkeypatch.setenv("MCP_SERVERS", '{"name": "x"}')
        assert load_mcp_configs() == []


class TestNormaliseMCPResult:
    def test_text_content(self):
        class TextBlock:
            type = "text"
            text = "Hello, world!"

        result = Mock(content=[TextBlock()], isError=False)
        data = _normalise_mcp_result(result)
        assert data["text"] == "Hello, world!"
        assert data["is_error"] is False

    def test_mixed_content(self):
        class TextBlock:
            type = "text"
            text = "Summary"

        class ResourceBlock:
            type = "resource"
            uri = "file:///tmp/out.png"
            mimeType = "image/png"

        result = Mock(content=[TextBlock(), ResourceBlock()], isError=False)
        data = _normalise_mcp_result(result)
        assert data["text"] == "Summary"
        assert len(data["resources"]) == 1
        assert data["resources"][0]["uri"] == "file:///tmp/out.png"

    def test_empty_content(self):
        result = Mock(content=[], isError=False)
        data = _normalise_mcp_result(result)
        assert data["text"] == ""


class TestRegisterMCPTools:
    def test_registers_new_tools(self):
        tool_registry.reset()
        # Force re-import to re-register music tools (bypasses import cache)
        import importlib
        import backend.tools.music_tool
        importlib.reload(backend.tools.music_tool)

        manager = Mock(spec=MCPClientManager)
        adapter = MCPToolAdapter(
            tool_schema={
                "name": "get_forecast",
                "description": "d",
                "inputSchema": {"type": "object", "properties": {}},
            },
            server_name="weather",
            manager=manager,
        )
        manager.iter_adapters.return_value = [adapter]

        count = asyncio_sync(register_mcp_tools(manager))
        assert count == 1
        assert "get_forecast" in tool_registry

    def test_skips_conflicting_tools(self):
        tool_registry.reset()
        import importlib
        import backend.tools.music_tool
        importlib.reload(backend.tools.music_tool)

        manager = Mock(spec=MCPClientManager)
        # Try to register a tool whose name conflicts with an existing local tool
        adapter = MCPToolAdapter(
            tool_schema={
                "name": "search_music",  # ← conflicts with SearchMusicTool
                "description": "d",
                "inputSchema": {"type": "object", "properties": {}},
            },
            server_name="external",
            manager=manager,
        )
        manager.iter_adapters.return_value = [adapter]

        count = asyncio_sync(register_mcp_tools(manager))
        assert count == 0  # skipped, not registered


def asyncio_sync(coro):
    """Helper: run a coroutine synchronously in tests."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If there's a running loop, we're inside pytest-asyncio — use a new loop
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as ex:
        future = ex.submit(asyncio.run, coro)
        return future.result()
