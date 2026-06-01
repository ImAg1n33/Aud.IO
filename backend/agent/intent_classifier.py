"""Hybrid intent classifier — LLM primary with keyword fallback (RFC-004).

RFC-007: Hard play signals short-circuit to MUSIC_PLAY before LLM/keyword paths,
eliminating song-title-vs-emotion ambiguity for phrases like "来一首嫉妒".
"""

import asyncio
import json
import logging
import os
from enum import Enum
from typing import ClassVar

import httpx

logger = logging.getLogger(__name__)

from backend.agent.prompts import INTENT_CLASSIFIER_SYSTEM

# ── LLM config ──

_CLASSIFY_TIMEOUT = 1.5
_CLASSIFY_MODEL = os.getenv("LLM_MODEL", "").strip() or "deepseek-v4-flash"
_CLASSIFY_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
_CLASSIFY_API_KEY = (
    os.getenv("LLM_API_KEY", "").strip()
    or os.getenv("DEEPSEEK_API_KEY", "").strip()
)


class Intent(str, Enum):
    MUSIC_PLAY = "music_play"
    MUSIC_RECOMMEND = "music_recommend"
    WEATHER = "weather"
    CHITCHAT = "chitchat"
    UNKNOWN = "unknown"


class IntentClassifier:
    # ── RFC-007: Hard play signals — 100% confidence, skip LLM ──────────
    HARD_PLAY_SIGNALS: ClassVar[list[str]] = [
        "来一首", "来首", "点一首", "点首", "放一首", "放首",
        "播一首", "播首", "换一首", "换首", "下一首", "切歌",
        "再来一首", "来点",
    ]

    MUSIC_PLAY_KEYWORDS: ClassVar[list[str]] = [
        "play", "put on", "start", "listen to",
        "放", "来一首", "来首", "播", "点一首", "点首", "放一首", "放首",
        "换一首", "换首", "下一首", "切歌", "再来一首", "来点",
    ]

    MUSIC_RECOMMEND_KEYWORDS: ClassVar[list[str]] = [
        "recommend", "suggest", "what should i listen", "what do you recommend",
        "any recommendations", "推荐", "有什么好听的", "适合",
        "心情", "累了", "开心", "难过", "低落", "兴奋", "焦虑", "放松",
        "想听", "给我推荐", "建议",
    ]

    WEATHER_KEYWORDS: ClassVar[list[str]] = [
        "weather", "天气", "下雨", "晴天", "下雪", "温度", "刮风",
        "阴天", "多云", "外面", "how's the weather",
    ]

    # ═══════════════════════════════════════════════════════════════
    # RFC-004: LLM primary path with 1.5s timeout fallback
    # ═══════════════════════════════════════════════════════════════

    async def classify_async(self, user_input: str) -> Intent:
        """Classify via LLM with keyword fallback.

        RFC-007: Hard play signals short-circuit to MUSIC_PLAY immediately.
        """
        # RFC-007: Hard play signals — user clearly wants playback, no LLM needed
        if self._has_hard_play_signal(user_input):
            return Intent.MUSIC_PLAY

        try:
            intent = await asyncio.wait_for(
                self._classify_via_llm(user_input),
                timeout=_CLASSIFY_TIMEOUT,
            )
            if isinstance(intent, Intent):
                return intent
        except Exception:
            logger.debug("LLM intent classify failed, using keyword fallback")
        return self.classify(user_input)

    async def _classify_via_llm(self, user_input: str) -> Intent:
        """Raw LLM call — raises on any failure so the caller can fall back."""
        if not _CLASSIFY_API_KEY:
            raise RuntimeError("LLM_API_KEY not configured")

        endpoint = f"{_CLASSIFY_BASE_URL.rstrip('/')}/chat/completions"
        body = {
            "model": _CLASSIFY_MODEL,
            "temperature": 0,
            "max_tokens": 10,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": INTENT_CLASSIFIER_SYSTEM},
                {"role": "user", "content": user_input},
            ],
        }

        async with httpx.AsyncClient(timeout=_CLASSIFY_TIMEOUT) as client:
            response = await client.post(
                endpoint,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_CLASSIFY_API_KEY}",
                },
            )
            response.raise_for_status()

        payload = response.json()
        label = payload["choices"][0]["message"]["content"].strip()
        parsed = json.loads(label)
        return Intent(parsed["intent"])

    # ═══════════════════════════════════════════════════════════════
    # Deterministic keyword path
    # ═══════════════════════════════════════════════════════════════

    def classify(self, user_input: str, conversation_history: list | None = None) -> Intent:
        lowered = user_input.strip().lower()
        if not lowered:
            return Intent.UNKNOWN

        # RFC-007: Hard play signals checked first (also covers classify_async path)
        if self._has_hard_play_signal(user_input):
            return Intent.MUSIC_PLAY

        # Music play — most actionable
        if self._matches_any(lowered, self.MUSIC_PLAY_KEYWORDS):
            if self._is_music_question(lowered):
                return Intent.MUSIC_RECOMMEND
            return Intent.MUSIC_PLAY

        # Music recommend
        if self._matches_any(lowered, self.MUSIC_RECOMMEND_KEYWORDS):
            return Intent.MUSIC_RECOMMEND

        if self._is_music_question(lowered):
            return Intent.MUSIC_RECOMMEND

        # Weather
        if self._matches_any(lowered, self.WEATHER_KEYWORDS):
            return Intent.WEATHER

        # Music-adjacent (genre words without action verbs)
        if self._is_music_adjacent(lowered):
            return Intent.MUSIC_RECOMMEND

        if self._is_chitchat(lowered):
            return Intent.CHITCHAT

        return Intent.UNKNOWN

    # ── RFC-007: Hard play signal detector ──────────────────────────────

    @classmethod
    def _has_hard_play_signal(cls, user_input: str) -> bool:
        """Check for unambiguous playback-request signals.

        Phrases like "来一首X" / "播放X" / "放首X" are treated as
        MUSIC_PLAY regardless of whether X looks like an emotion word.
        """
        return any(sig in user_input for sig in cls.HARD_PLAY_SIGNALS)

    # ── Intent gating helpers ──────────────────────────────────────────

    def should_include_preferences(self, intent: Intent) -> bool:
        return intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

    def should_activate_tool_categories(self, intent: Intent) -> list[str]:
        mapping: dict[Intent, list[str]] = {
            Intent.MUSIC_PLAY: ["music"],
            Intent.MUSIC_RECOMMEND: ["music"],
            Intent.WEATHER: ["weather"],
            Intent.CHITCHAT: [],
            Intent.UNKNOWN: ["music", "weather", "tts"],
        }
        return mapping.get(intent, [])

    # ── Static helpers ─────────────────────────────────────────────────

    @staticmethod
    def _matches_any(text: str, keywords: list[str]) -> bool:
        return any(kw in text for kw in keywords)

    @staticmethod
    def _is_music_question(text: str) -> bool:
        question_signals = ["what", "recommend", "suggest", "推荐", "有什么"]
        music_signals = ["song", "music", "listen", "playlist", "歌", "音乐", "听"]
        has_question = any(sig in text for sig in question_signals)
        has_music = any(sig in text for sig in music_signals)
        return has_question and has_music

    @staticmethod
    def _is_music_adjacent(text: str) -> bool:
        music_signals = [
            "song", "music", "track", "album", "playlist",
            "歌", "曲", "音乐", "专辑", "歌单",
            "jazz", "rock", "pop", "hip", "hop", "classical",
            "lofi", "ambient", "electronic", "folk", "blues",
            "rnb", "country", "metal", "punk", "indie",
        ]
        return any(sig in text for sig in music_signals)

    @staticmethod
    def _is_chitchat(text: str) -> bool:
        chitchat_signals = [
            "hello", "hi", "hey", "thanks", "thank you", "bye",
            "how are you", "who are you", "what can you do",
            "你好", "谢谢", "再见", "你是谁", "帮我", "怎么做",
        ]
        return any(sig in text for sig in chitchat_signals)
