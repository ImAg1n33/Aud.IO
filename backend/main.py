import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes_agent import router as agent_router


load_dotenv(Path(__file__).resolve().parent / ".env")


app = FastAPI(title="Aud.IO API", version="0.1.0")


def _parse_cors_origins(value: str | None) -> list[str]:
    default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
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
def ready() -> dict[str, bool]:
    return {"ready": True}
