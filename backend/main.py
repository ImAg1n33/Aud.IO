import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# ── .env 加载时序（幂等） ─────────────────────────────────────────────
# backend/config.py 的 Settings 自带 load_dotenv；此处显式加载保持与
# 历史行为一致（routes_agent 的 import-time AssistantService 单例依赖它）。
load_dotenv(Path(__file__).resolve().parent / ".env")

from backend.data_config import ensure_data_dirs, get_data_dir  # noqa: E402
ensure_data_dirs()

from backend.api.routes_agent import router as agent_router  # noqa: E402
from backend.agent.llm_client import _get_llm_config  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.tools.mcp_adapter import MCPClientManager, register_mcp_tools  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

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


cors_origins = _parse_cors_origins(settings.cors_allow_origins)

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
    embedding_provider = settings.embedding_provider.strip().lower()
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
        settings.netease_api_base_url.strip()
        or settings.netease_cookie.strip()
    )

    # ── LLM 调用指标（P2-1 结构化日志） ─────────────────────────
    llm_metrics: dict = {"total": 0, "failed": 0, "avg_latency_ms": None}
    try:
        log_path = get_data_dir() / "llm_calls.jsonl"
        if log_path.exists():
            import json as _json

            records = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    records.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
            today = []
            for r in records:
                import time as _time
                if _time.time() - r.get("ts", 0) < 86400:
                    today.append(r)
            if today:
                failed = sum(1 for r in today if not r.get("ok", True))
                llm_metrics = {
                    "total": len(today),
                    "failed": failed,
                    "avg_latency_ms": round(
                        sum(r.get("latency_ms", 0) for r in today) / len(today), 1
                    ),
                }
    except Exception:
        pass

    return {
        "ready": True,
        "version": app.version,
        "llm": llm_status,
        "embedding": embedding_status,
        "chromadb": chroma_status,
        "netease": {"configured": netease_configured},
        "mcp": {"servers": mcp_servers, "tools": mcp_tools},
        "metrics": {"llm_calls_24h": llm_metrics},
    }
