import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes_agent import router as agent_router
from backend.tools.mcp_adapter import MCPClientManager, register_mcp_tools

logger = logging.getLogger(__name__)

# ================================================================
# MCP lifecycle (RFC-001)
# ================================================================

_mcp_manager: MCPClientManager | None = None


async def _startup_mcp() -> None:
    global _mcp_manager
    _mcp_manager = MCPClientManager.from_env()
    await _mcp_manager.start_all()
    if _mcp_manager.server_count > 0:
        registered = await register_mcp_tools(_mcp_manager)
        logger.info(
            "MCP layer ready: %d server(s), %d tool(s) registered",
            _mcp_manager.server_count, registered,
        )


async def _shutdown_mcp() -> None:
    global _mcp_manager
    if _mcp_manager is not None:
        await _mcp_manager.stop_all()
        _mcp_manager = None


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    await _startup_mcp()
    yield
    await _shutdown_mcp()


# ================================================================
# App
# ================================================================

app = FastAPI(title="Aud.IO API", version="0.2.0", lifespan=_app_lifespan)


def _parse_cors_origins(value: str | None) -> list[str]:
    default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "null",
    ]
    if not value:
        return default_origins

    origins = [item.strip() for item in value.split(",") if item.strip()]
    return origins or default_origins


cors_origins = _parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    mcp_servers = _mcp_manager.server_count if _mcp_manager else 0
    mcp_tools = _mcp_manager.adapter_count if _mcp_manager else 0
    return {
        "ready": True,
        "mcp_servers": mcp_servers,
        "mcp_tools": mcp_tools,
    }
