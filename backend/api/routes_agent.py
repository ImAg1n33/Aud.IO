import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse
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


@router.post("/respond/stream")
async def agent_respond_stream(payload: AgentRequest, background_tasks: BackgroundTasks):
    """SSE streaming endpoint — returns text tokens in real-time, then music data.

    The frontend receives:
      event: token  → displayable answer text (typewriter effect)
      event: music  → JSON music object (trigger playback)
      event: done   → JSON full reply (debug panel)
    """
    async def generate():
        full_reply: dict[str, Any] = {}
        async for sse_msg in assistant_service.generate_reply_stream(
            payload.user_input, payload.context
        ):
            # Capture the final reply from the done event for profile update
            if sse_msg.startswith("event: done"):
                try:
                    data_str = sse_msg.split("data: ", 1)[1].strip()
                    full_reply = json.loads(data_str)
                except (json.JSONDecodeError, IndexError):
                    pass
            yield sse_msg

        if full_reply:
            assistant_service.schedule_profile_update(
                background_tasks, payload.user_input, full_reply
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
