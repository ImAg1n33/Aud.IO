from pathlib import Path

from fastapi import FastAPI
from dotenv import load_dotenv
from pydantic import BaseModel

from backend.agent.llm_client import call_llm
from backend.agent.prompt_builder import build_prompt


load_dotenv(Path(__file__).resolve().parent / ".env")


app = FastAPI(title="Aud.IO API", version="0.1.0")


class AgentRequest(BaseModel):
    user_input: str
    context: dict[str, str] | None = None


class AgentResponse(BaseModel):
    reply: str
    prompt: str


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
    return AgentResponse(reply=reply, prompt=prompt)
