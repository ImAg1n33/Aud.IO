"""会话反射（Reflection）—— 把短时对话压成结构化摘要，跨会话不失忆。

RFC: 反馈闭环同批的 v5 记忆升级。
流程: 每 N 轮触发 → LLM 生成 {summary, topics, song_signals} → Pydantic 校验
→ 写入 session_summaries 表。下次会话由 SessionSummaryProvider 注入。
失败静默降级（不阻断对话），与记忆观察者的容错哲学一致。
"""

import logging
from typing import Any

from backend.agent.llm_client import request_json_object
from backend.agent.prompts import build_reflection_messages
from backend.memory.episodic_memory import EpisodicMemory

logger = logging.getLogger(__name__)

VALID_SIGNALS = {"liked", "disliked", "mentioned"}


class SessionReflector:
    """会话摘要生成与持久化。"""

    def __init__(self, episodic: EpisodicMemory, model: str | None = None) -> None:
        self._episodic = episodic
        self._model = model

    async def summarize(
        self, transcript: str, turn_count: int,
    ) -> dict[str, Any] | None:
        """LLM 生成结构化摘要，任何失败返回 None（静默降级）。"""
        messages = build_reflection_messages(transcript, turn_count)
        try:
            parsed = await request_json_object(messages=messages, model=self._model, temperature=0.1)
        except Exception as exc:
            logger.warning("Reflection LLM 调用失败: %s", exc)
            return None

        summary_text = str(parsed.get("summary") or "").strip()
        topics = parsed.get("topics", [])
        song_signals = parsed.get("song_signals", [])

        # 清洗：类型与信号白名单（Pydantic 级别防护的轻量版）
        if not isinstance(topics, list):
            topics = []
        topics = [str(t) for t in topics if str(t).strip()][:10]

        cleaned_signals: list[dict[str, Any]] = []
        if isinstance(song_signals, list):
            for item in song_signals:
                if not isinstance(item, dict):
                    continue
                song = str(item.get("song") or "").strip()
                signal = str(item.get("signal") or "").strip().lower()
                if song and signal in VALID_SIGNALS:
                    cleaned_signals.append({"song": song, "signal": signal})
        song_signals = cleaned_signals[:10]

        if not summary_text:
            return None
        return {
            "summary": summary_text,
            "topics": topics,
            "song_signals": song_signals,
        }

    async def summarize_and_store(
        self, session_id: str, transcript: str, turn_count: int,
    ) -> int | None:
        """生成摘要并入库。返回摘要 ID，失败返回 None。"""
        result = await self.summarize(transcript, turn_count)
        if result is None:
            return None
        summary_id = await self._episodic.insert_session_summary(
            session_id=session_id,
            summary_text=result["summary"],
            topics=result["topics"],
            song_signals=result["song_signals"],
            turn_count=turn_count,
        )
        logger.info(
            "Reflection 完成: session=%s, id=%d, 摘要 %d 字, %d 个话题",
            session_id, summary_id, len(result["summary"]), len(result["topics"]),
        )
        return summary_id