import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes_agent import router as agent_router
from backend.agent.llm_client import _get_llm_config
from backend.data_config import ensure_data_dirs, get_data_dir
from backend.tools.mcp_adapter import MCPClientManager, register_mcp_tools

load_dotenv(Path(__file__).resolve().parent / ".env")
ensure_data_dirs()

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

app = FastAPI(title="Aud.IO API", version="0.3.1", lifespan=_app_lifespan)


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
async def ready() -> dict:
    mcp_servers = _mcp_manager.server_count if _mcp_manager else 0
    mcp_tools = _mcp_manager.adapter_count if _mcp_manager else 0

    # ── LLM config ──────────────────────────────────────────────
    llm_cfg = _get_llm_config()
    llm_status: dict = {
        "provider": llm_cfg["provider"],
        "model": llm_cfg["model"],
        "base_url": llm_cfg["base_url"],
        "configured": bool(llm_cfg["api_key"]),
    }

    # ── Embedding provider ──────────────────────────────────────
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
    embedding_status: dict = {"provider": embedding_provider}

    # ── ChromaDB ────────────────────────────────────────────────
    chroma_status: dict = {"ok": False, "documents": 0}
    try:
        import chromadb
        chroma_path = str(get_data_dir() / "chroma_episodes")
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_or_create_collection("episodes")
        chroma_status = {"ok": True, "documents": collection.count()}
    except Exception:
        pass

    # ── NetEase API ─────────────────────────────────────────────
    netease_configured = bool(
        os.getenv("NETEASE_API_BASE_URL", "").strip()
        or os.getenv("NETEASE_COOKIE", "").strip()
    )

    return {
        "ready": True,
        "version": app.version,
        "llm": llm_status,
        "embedding": embedding_status,
        "chromadb": chroma_status,
        "netease": {"configured": netease_configured},
        "mcp": {"servers": mcp_servers, "tools": mcp_tools},
    }
