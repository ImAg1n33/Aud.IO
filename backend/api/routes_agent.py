import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api._security import normalize_session_id
from backend.services.assistant_service import AssistantService
from backend.tools.netease_api import get_song_mp3_url


router = APIRouter(prefix="/v1/agent", tags=["agent"])
assistant_service = AssistantService()


class AgentRequest(BaseModel):
    user_input: str
    context: dict[str, Any] | None = None
    session_id: str | None = None


class AgentResponse(BaseModel):
    reply: dict[str, Any]
    prompt: str


class ReplayRequest(BaseModel):
    """按歌曲 ID 回放（RFC-020 契约 C）。"""

    song_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = None


class FeedbackRequest(BaseModel):
    """播放反馈事件 —— 前端播放器上报，校准记忆重要性。"""

    event: Literal[
        "song_started", "song_finished", "song_skipped",
        "song_disliked", "song_failed",
    ]
    song_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = None
    listen_seconds: float | None = Field(default=None, ge=0, le=86400)


class FeedbackResponse(BaseModel):
    ok: bool
    matched_snapshot_id: int | None = None
    disliked_artist: str | None = None


@router.post("/feedback", response_model=FeedbackResponse)
async def agent_feedback(payload: FeedbackRequest) -> FeedbackResponse:
    """接收播放结果反馈（started/finished/skipped/disliked/failed）。

    后端据此校准对应记忆快照的 importance_score —— DJ 从用户的真实
    听歌行为（完整听完 vs 切歌）中学习，而不是只依赖启发式权重。
    song_disliked 为显式信号：强降权 + 确定性写入画像 disliked（拒绝学习）。
    """
    try:
        sid = normalize_session_id(payload.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    matched_id = await assistant_service.episodic_memory.record_play_feedback(
        session_id=sid,
        song_id=payload.song_id,
        event=payload.event,
        listen_seconds=payload.listen_seconds,
    )

    disliked_artist: str | None = None
    if payload.event == "song_disliked" and matched_id is not None:
        song_info = await assistant_service.episodic_memory.get_song_info_by_feedback(
            sid, payload.song_id,
        )
        artist = (song_info or {}).get("artist")
        if artist:
            ctx = assistant_service.session_manager.get_or_create(sid)
            assistant_service._ensure_memory_manager(sid)
            if ctx.memory_manager.add_disliked_artist(artist):
                disliked_artist = artist

    return FeedbackResponse(
        ok=True,
        matched_snapshot_id=matched_id,
        disliked_artist=disliked_artist,
    )


@router.post("/respond", response_model=AgentResponse)
async def agent_respond(payload: AgentRequest, background_tasks: BackgroundTasks) -> AgentResponse:
    try:
        sid = normalize_session_id(payload.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    final_reply, prompt = await assistant_service.generate_reply(
        payload.user_input, payload.context, session_id=sid,
    )
    assistant_service.schedule_profile_update(
        background_tasks, payload.user_input, final_reply, session_id=sid,
    )
    return AgentResponse(reply=final_reply, prompt=prompt)


@router.post("/respond/stream")
async def agent_respond_stream(payload: AgentRequest, background_tasks: BackgroundTasks):
    """SSE streaming endpoint — returns text tokens in real-time, then music data.

    The frontend receives:
      event: token  → displayable answer text (typewriter effect)
      event: music  → JSON music object (trigger playback)
      event: done   → JSON full reply (debug panel)
    """
    try:
        sid = normalize_session_id(payload.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async def generate():
        full_reply: dict[str, Any] = {}
        async for sse_msg in assistant_service.generate_reply_stream(
            payload.user_input, payload.context, session_id=sid,
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
                background_tasks, payload.user_input, full_reply,
                session_id=sid,
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


@router.get("/history")
async def agent_history(
    scope: Literal["session", "global"] = "global",
    limit: int = 50,
    session_id: str | None = None,
) -> dict[str, Any]:
    """播放历史（RFC-020 契约 C）。

    scope=session → 本会话歌单缓存（需 session_id）；scope=global → 跨会话最近播放。
    只返回元数据；回放请走 POST /replay（mp3 URL 会过期，不缓存）。
    """
    sid: str | None = None
    if scope == "session":
        if not session_id:
            raise HTTPException(
                status_code=400, detail="session_id is required for scope=session",
            )
        try:
            sid = normalize_session_id(session_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    songs = await assistant_service.play_history.list_async(sid, limit=limit)
    return {"scope": scope, "songs": songs}


@router.post("/replay")
async def agent_replay(payload: ReplayRequest) -> dict[str, Any]:
    """按 song_id 回放（RFC-020 契约 C）—— 重新解析 mp3_url 后返回。

    列表模式本地切歌 / 历史面板点播走这里；不经过 LLM。
    回放同样写入播放历史（该曲成为"最近"）。
    """
    try:
        sid = normalize_session_id(payload.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = await assistant_service.play_history.find_async(payload.song_id, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="song not found in play history")

    try:
        mp3_url = await get_song_mp3_url(payload.song_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to resolve audio url: {exc}")

    await assistant_service.play_history.add_async(
        sid, row, str(row.get("requested_keyword") or ""),
    )

    return {
        "song_id": row["song_id"],
        "name": row["name"],
        "artist": row["artist"],
        "cover_url": row["cover_url"],
        "album": row["album"],
        "duration_ms": row["duration_ms"],
        "requested_keyword": row.get("requested_keyword", ""),
        "mp3_url": mp3_url,
    }
