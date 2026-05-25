"""MCP (Model Context Protocol) adapter layer — bridges external MCP servers
into Aud.IO's native ToolRegistry via the Adapter Pattern.

v0.3 RFC-001 implementation.  Supported transports: stdio (primary).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from backend.tools.base import BaseTool, ToolExecutionError, ToolResult, tool_registry

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================


def load_mcp_configs() -> list[dict[str, Any]]:
    """Parse MCP_SERVERS env var into validated config dicts."""
    raw = os.getenv("MCP_SERVERS", "").strip()
    if not raw or raw == "[]":
        return []

    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("MCP_SERVERS is not valid JSON: %s", exc)
        return []

    if not isinstance(servers, list):
        logger.error("MCP_SERVERS must be a JSON array, got %s", type(servers).__name__)
        return []

    configs: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            logger.info("MCP server '%s' is disabled, skipping", item.get("name", "?"))
            continue
        configs.append(item)
    return configs


# ============================================================
# MCPToolAdapter — wraps a remote MCP tool as a local BaseTool
# ============================================================


class MCPToolAdapter(BaseTool):
    """Make an MCP tool look like a native Aud.IO tool.

    ToolRegistry and ToolExecutor see no difference between this and a
    hand-written BaseTool subclass — same .name, .description, .parameters,
    and async .execute(**kwargs).
    """

    def __init__(
        self,
        tool_schema: dict[str, Any],
        server_name: str,
        manager: "MCPClientManager",
    ) -> None:
        self.name = tool_schema["name"]
        self.description = tool_schema.get("description", "")
        self.parameters = tool_schema.get("inputSchema", {"type": "object", "properties": {}})
        self._server_name = server_name
        self._manager = manager

    def is_available(self) -> bool:
        return self._manager.is_connected(self._server_name)

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            result = await asyncio.wait_for(
                self._manager.call_tool(self._server_name, self.name, kwargs),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            return ToolResult.fail(
                ToolExecutionError(f"MCP tool '{self.name}' timed out after 30s"),
            )
        except Exception as exc:
            return ToolResult.fail(
                ToolExecutionError(f"MCP tool '{self.name}' failed: {exc}"),
            )

        # result is a dict from _normalise_mcp_result
        return ToolResult.ok(result, provider="mcp", server=self._server_name)


# ============================================================
# MCPClientManager — lifecycle, discovery, routing
# ============================================================


@dataclass
class _ServerState:
    """Internal bookkeeping for one connected MCP server."""

    config: dict[str, Any]
    session: Any = None          # ClientSession
    stdio_ctx: Any = None        # stdio_client async context manager
    session_ctx: Any = None      # ClientSession async context manager
    adapters: list[MCPToolAdapter] | None = None


class MCPClientManager:
    """Manages MCP server lifecycles — connect, discover, serve tool calls.

    Usage in main.py::

        manager = MCPClientManager.from_env()
        await manager.start_all()
        for adapter in manager.iter_adapters():
            tool_registry.register(adapter)

        # ... app runs ...

        await manager.stop_all()
    """

    def __init__(self, configs: list[dict[str, Any]] | None = None) -> None:
        self._configs = configs or []
        self._servers: dict[str, _ServerState] = {}

    @classmethod
    def from_env(cls) -> "MCPClientManager":
        return cls(load_mcp_configs())

    # ---- lifecycle ----

    async def start_all(self) -> None:
        """Connect to every configured MCP server, discover & build adapters."""
        if not self._configs:
            logger.info("No MCP servers configured, skipping MCP layer")
            return

        for cfg in self._configs:
            name = cfg.get("name", "unnamed")
            transport = cfg.get("transport", "stdio")

            if transport != "stdio":
                logger.warning(
                    "MCP server '%s': transport '%s' not yet supported, skipped",
                    name, transport,
                )
                continue

            state = _ServerState(config=cfg)
            self._servers[name] = state

            try:
                await self._connect_stdio(state)
                tools = await state.session.list_tools()
                state.adapters = [
                    MCPToolAdapter(tool.model_dump(), name, self)
                    for tool in tools.tools
                ]
                tool_names = [a.name for a in state.adapters]
                logger.info(
                    "MCP server '%s' connected — %d tools: %s",
                    name, len(state.adapters), tool_names,
                )
            except Exception as exc:
                logger.error(
                    "MCP server '%s' failed to start: %s — tools unavailable",
                    name, exc,
                )
                # Keep state but without adapters → is_available() returns False

    async def stop_all(self) -> None:
        """Gracefully close all MCP connections and terminate child processes."""
        for name, state in list(self._servers.items()):
            try:
                if state.session_ctx is not None:
                    await state.session_ctx.__aexit__(None, None, None)
                if state.stdio_ctx is not None:
                    await state.stdio_ctx.__aexit__(None, None, None)
                logger.info("MCP server '%s' shut down", name)
            except Exception as exc:
                logger.warning("Error shutting down MCP server '%s': %s", name, exc)
        self._servers.clear()

    # ---- query ----

    def is_connected(self, server_name: str) -> bool:
        state = self._servers.get(server_name)
        return state is not None and state.session is not None

    def iter_adapters(self) -> list[MCPToolAdapter]:
        """Flat list of all discovered MCP tool adapters ready for registration."""
        result: list[MCPToolAdapter] = []
        for state in self._servers.values():
            if state.adapters:
                result.extend(state.adapters)
        return result

    @property
    def server_count(self) -> int:
        return len(self._servers)

    @property
    def adapter_count(self) -> int:
        return sum(
            len(s.adapters) for s in self._servers.values() if s.adapters
        )

    # ---- tool execution ----

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Forward a tool call to the correct MCP server session."""
        state = self._servers.get(server_name)
        if state is None or state.session is None:
            raise ToolExecutionError(
                f"MCP server '{server_name}' is not connected"
            )
        result = await state.session.call_tool(tool_name, arguments=arguments)
        return _normalise_mcp_result(result)

    # ---- internal ----

    async def _connect_stdio(self, state: _ServerState) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        cfg = state.config
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env={**os.environ, **cfg.get("env", {})} if cfg.get("env") else None,
        )

        # Enter both context managers and stash the exit callbacks
        state.stdio_ctx = stdio_client(params)
        read, write = await state.stdio_ctx.__aenter__()

        state.session_ctx = ClientSession(read, write)
        state.session = await state.session_ctx.__aenter__()
        await state.session.initialize()


# ============================================================
# Helpers
# ============================================================


def _normalise_mcp_result(result: Any) -> dict[str, Any]:
    """Convert MCP CallToolResult into a plain dict for ToolResult.data."""
    data: dict[str, Any] = {}
    try:
        data["is_error"] = getattr(result, "isError", False)
        content = getattr(result, "content", [])
        texts: list[str] = []
        for block in content:
            block_type = getattr(block, "type", "text")
            if block_type == "text":
                texts.append(getattr(block, "text", ""))
            elif block_type == "resource":
                data.setdefault("resources", []).append({
                    "uri": getattr(block, "uri", ""),
                    "mimeType": getattr(block, "mimeType", ""),
                })
        data["text"] = "\n".join(texts) if texts else ""
    except Exception:
        data["raw"] = str(result)
    return data


# ============================================================
# Convenience: register all MCP adapters into the global registry
# ============================================================


async def register_mcp_tools(manager: MCPClientManager) -> int:
    """Register all discovered MCP tools into the global tool_registry.

    Local tools take priority — if a tool name is already registered,
    the MCP version is skipped with a warning.
    Returns the count of successfully registered adapters.
    """
    registered = 0
    for adapter in manager.iter_adapters():
        if adapter.name in tool_registry:
            logger.warning(
                "MCP tool '%s' conflicts with existing local tool, skipping",
                adapter.name,
            )
            continue
        tool_registry.register(adapter)
        registered += 1

    logger.info("Registered %d MCP tools into global registry", registered)
    return registered
