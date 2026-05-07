from typing import Any

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from backend.services.assistant_service import AssistantService


router = APIRouter(prefix="/v1/agent", tags=["agent"])
assistant_service = AssistantService()


class AgentRequest(BaseModel):
    user_input: str
    context: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    reply: dict[str, Any]
    prompt: str


@router.post("/respond", response_model=AgentResponse)
async def agent_respond(payload: AgentRequest, background_tasks: BackgroundTasks) -> AgentResponse:
    final_reply, prompt = await assistant_service.generate_reply(payload.user_input, payload.context)
    assistant_service.schedule_profile_update(background_tasks, payload.user_input, final_reply)
    return AgentResponse(reply=final_reply, prompt=prompt)
