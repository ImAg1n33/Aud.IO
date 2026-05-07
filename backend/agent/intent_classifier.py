"""Lightweight, rule-based intent classifier — zero LLM cost."""

from enum import Enum
from typing import ClassVar


class Intent(str, Enum):
    MUSIC_PLAY = "music_play"          # "play", "put on", "播放"
    MUSIC_RECOMMEND = "music_recommend" # "recommend", "what should I listen to"
    WEATHER = "weather"                # "weather", "天气"
    CHITCHAT = "chitchat"             # casual conversation
    UNKNOWN = "unknown"


class IntentClassifier:
    MUSIC_PLAY_KEYWORDS: ClassVar[list[str]] = [
        # English
        "play", "put on", "start", "listen to", "播放",
        # Chinese
        "放", "来一首", "来首", "播", "点一首", "点首", "放一首", "放首",
        "换一首", "换首", "下一首", "切歌", "再来一首", "来点",
    ]

    MUSIC_RECOMMEND_KEYWORDS: ClassVar[list[str]] = [
        # English
        "recommend", "suggest", "what should i listen", "what do you recommend",
        "any recommendations", "推荐", "有什么好听的", "适合",
        # Chinese mood-based
        "心情", "累了", "开心", "难过", "低落", "兴奋", "焦虑", "放松",
        "想听", "给我推荐", "建议",
    ]

    WEATHER_KEYWORDS: ClassVar[list[str]] = [
        "weather", "天气", "下雨", "晴天", "下雪", "温度", "刮风",
        "阴天", "多云", "外面", "how's the weather",
    ]

    def classify(self, user_input: str, conversation_history: list | None = None) -> Intent:
        """Classify user input into an intent category.

        Priority: MUSIC_PLAY > WEATHER > MUSIC_RECOMMEND > CHITCHAT > UNKNOWN
        """
        lowered = user_input.strip().lower()
        if not lowered:
            return Intent.UNKNOWN

        # Music play — most actionable.
        # But if it's a question about what to play, it's a recommendation.
        if self._matches_any(lowered, self.MUSIC_PLAY_KEYWORDS):
            if self._is_music_question(lowered):
                return Intent.MUSIC_RECOMMEND
            return Intent.MUSIC_PLAY

        # Music recommend (before weather — "rainy day music" is about music, not weather)
        if self._matches_any(lowered, self.MUSIC_RECOMMEND_KEYWORDS):
            return Intent.MUSIC_RECOMMEND

        # Check for question-form music queries
        if self._is_music_question(lowered):
            return Intent.MUSIC_RECOMMEND

        # Weather — only when weather is the primary topic
        if self._matches_any(lowered, self.WEATHER_KEYWORDS):
            return Intent.WEATHER

        # If contains music-related but no specific action, it's a recommendation
        if self._is_music_adjacent(lowered):
            return Intent.MUSIC_RECOMMEND

        # Check if it's a chitchat
        if self._is_chitchat(lowered):
            return Intent.CHITCHAT

        return Intent.UNKNOWN

    def should_include_preferences(self, intent: Intent) -> bool:
        """Whether to inject user preference data for this intent."""
        return intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

    def should_activate_tool_categories(self, intent: Intent) -> list[str]:
        """Which tool categories to enable for this intent."""
        mapping: dict[Intent, list[str]] = {
            Intent.MUSIC_PLAY: ["music"],
            Intent.MUSIC_RECOMMEND: ["music"],
            Intent.WEATHER: ["weather"],
            Intent.CHITCHAT: [],
            Intent.UNKNOWN: ["music", "weather", "tts"],
        }
        return mapping.get(intent, [])

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
