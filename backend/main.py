from pathlib import Path
from typing import Any

from fastapi import FastAPI
from dotenv import load_dotenv
from pydantic import BaseModel

from backend.agent.llm_client import call_llm
from backend.agent.prompt_builder import build_prompt
from backend.tools.netease_api import get_song_mp3_url, search_first_song


load_dotenv(Path(__file__).resolve().parent / ".env")


app = FastAPI(title="Aud.IO API", version="0.1.0")


class AgentRequest(BaseModel):
    user_input: str
    context: dict[str, str] | None = None


class AgentResponse(BaseModel):
    reply: dict[str, Any]
    prompt: str


def _extract_play_keyword(reply: dict[str, Any]) -> str:
    direct = reply.get("play_keyword")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    actions = reply.get("actions", [])
    if not isinstance(actions, list):
        return ""

    for item in actions:
        if isinstance(item, dict):
            action_type = str(item.get("type", "")).strip().lower()
            if action_type in {"play_music", "play_song", "music_search"}:
                keyword = item.get("keyword") or item.get("play_keyword")
                if isinstance(keyword, str) and keyword.strip():
                    return keyword.strip()
            continue

        if not isinstance(item, str):
            continue

        text = item.strip()
        if not text:
            continue

        for prefix in ["play_music:", "play_song:", "music_search:", "play:", "播放:"]:
            if text.lower().startswith(prefix.lower()):
                keyword = text[len(prefix) :].strip()
                if keyword:
                    return keyword

    return ""


def _attach_music_result(reply: dict[str, Any]) -> dict[str, Any]:
    keyword = _extract_play_keyword(reply)
    if not keyword:
        return reply

    enriched = dict(reply)
    try:
        song = search_first_song(keyword)
        mp3_url = get_song_mp3_url(song["id"])
        enriched["music"] = {
            "requested_keyword": keyword,
            "song_id": song["id"],
            "name": song["name"],
            "artist": song["artist"],
            "mp3_url": mp3_url,
        }
    except Exception as exc:
        enriched["music"] = {
            "requested_keyword": keyword,
            "error": str(exc),
        }
    return enriched


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, bool]:
    return {"ready": True}


@app.post("/v1/agent/respond", response_model=AgentResponse)
def agent_respond(payload: AgentRequest) -> AgentResponse:
    prompt = build_prompt(payload.user_input, payload.context or {})
    reply = call_llm(prompt)
    final_reply = _attach_music_result(reply)
    return AgentResponse(reply=final_reply, prompt=prompt)
