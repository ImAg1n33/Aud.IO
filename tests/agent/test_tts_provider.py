import pytest

from backend.agent.tts_provider import TTSProvider
from backend.config import settings


class TestSegmentation:
    """_segment() — pure function, easy to exhaustively test."""

    def test_empty_text_returns_empty(self) -> None:
        provider = TTSProvider()
        assert provider._segment("") == []
        assert provider._segment("   ") == []

    def test_splits_on_period(self) -> None:
        provider = TTSProvider()
        result = provider._segment("Hey there. Let's play some jazz.")
        assert result == ["Hey there", "Let's play some jazz"]

    def test_splits_on_chinese_period(self) -> None:
        provider = TTSProvider()
        result = provider._segment("来首轻松的爵士吧。再来一首Miles Davis。")
        assert result == ["来首轻松的爵士吧", "再来一首Miles Davis"]

    def test_splits_on_exclamation_and_question(self) -> None:
        provider = TTSProvider()
        result = provider._segment("Nice choice! What next? Let's go.")
        assert len(result) == 3
        assert result[0] == "Nice choice"
        assert result[1] == "What next"
        assert result[2] == "Let's go"

    def test_splits_on_newline(self) -> None:
        provider = TTSProvider()
        result = provider._segment("Line one\nLine two")
        assert result == ["Line one", "Line two"]

    def test_single_sentence_passes_through(self) -> None:
        provider = TTSProvider()
        result = provider._segment("Just one sentence without punctuation")
        assert result == ["Just one sentence without punctuation"]

    def test_splits_long_on_comma(self) -> None:
        provider = TTSProvider()
        # 380 chars with a comma in the middle — each half < 200 so no hard cut
        long_text = "A" * 180 + "，" + "B" * 195
        result = provider._segment(long_text, max_len=200)
        assert len(result) == 2
        assert all(len(s) <= 200 for s in result)

    def test_hard_cuts_when_no_delimiter(self) -> None:
        provider = TTSProvider()
        long_text = "A" * 250  # no punctuation at all
        result = provider._segment(long_text, max_len=200)
        assert len(result) == 2
        assert all(len(s) <= 200 for s in result)

    def test_hard_cut_breaks_at_space(self) -> None:
        provider = TTSProvider()
        long_text = "x" * 190 + " hello " + "y" * 100
        result = provider._segment(long_text, max_len=200)
        assert len(result) >= 1
        assert all(len(s) <= 200 for s in result)


class TestPreRollText:
    """pre_roll_text() — first-sentence extraction for DJ short tag."""

    def test_returns_first_sentence(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        result = provider.pre_roll_text(
            "Hello beautiful people. Today is rainy. Let's play some jazz."
        )
        assert result == "Hello beautiful people"

    def test_respects_max_len(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        result = provider.pre_roll_text(
            "A" * 50 + " " + "B" * 50 + ". " + "C" * 50, max_len=80,
        )
        assert result is not None
        assert len(result) <= 80

    def test_returns_none_for_short_text(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        assert provider.pre_roll_text("Hi") is None
        assert provider.pre_roll_text("  Hello.  ") is None  # 5 chars stripped

    def test_returns_none_for_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        assert provider.pre_roll_text("") is None
        assert provider.pre_roll_text(None) is None

    def test_returns_none_when_disabled(self) -> None:
        # pre_roll_text checks is_enabled first — returns None when TTS is off
        provider = TTSProvider()
        assert provider.pre_roll_text("Hello world. Nice.") is None


class TestFeatureGates:
    """is_enabled / intent_enabled — env-var driven gates."""

    def test_disabled_by_default(self) -> None:
        provider = TTSProvider()
        assert provider.is_enabled is False

    def test_enabled_when_env_is_true(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        assert provider.is_enabled is True

    def test_intent_in_whitelist(self) -> None:
        provider = TTSProvider()
        assert provider.intent_enabled("chitchat") is True
        assert provider.intent_enabled("weather") is True

    def test_intent_not_in_whitelist(self) -> None:
        provider = TTSProvider()
        assert provider.intent_enabled("music_play") is False
        assert provider.intent_enabled("music_recommend") is False

    def test_custom_intents_from_env(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_intents", "chitchat,music_play")
        provider = TTSProvider()
        assert provider.intent_enabled("chitchat") is True
        assert provider.intent_enabled("music_play") is True
        assert provider.intent_enabled("weather") is False


class TestSynthesize:
    """synthesize() — integration with ToolRegistry."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self) -> None:
        provider = TTSProvider()
        result = await provider.synthesize("Hello world")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_text(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider()
        assert await provider.synthesize("") == []
        assert await provider.synthesize("   ") == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_tool_not_registered(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "tts_enabled", True)
        provider = TTSProvider(tool_name="nonexistent_tts")
        result = await provider.synthesize("Hello.")
        assert result == []

    @pytest.mark.asyncio
    async def test_calls_tool_and_returns_urls(self, monkeypatch) -> None:
        from backend.tools.base import BaseTool, ToolResult, tool_registry

        monkeypatch.setattr(settings, "tts_enabled", True)

        class FakeTTSTool(BaseTool):
            name = "fake_tts"
            description = "Fake TTS for testing"
            parameters = {}

            async def execute(self, **kwargs):
                text = kwargs.get("text", "")
                return ToolResult.ok({"url": f"https://tts.example/{hash(text)}.mp3"})

        tool_registry.register(FakeTTSTool())
        try:
            provider = TTSProvider(tool_name="fake_tts")
            result = await provider.synthesize("Hello. World.", max_len=200)
            assert len(result) == 2  # two sentences
            for url in result:
                assert url.startswith("https://tts.example/")
        finally:
            tool_registry.reset()

    @pytest.mark.asyncio
    async def test_skips_failed_segment_continues_next(self, monkeypatch) -> None:
        from backend.tools.base import BaseTool, ToolResult, ToolExecutionError, tool_registry

        monkeypatch.setattr(settings, "tts_enabled", True)
        call_count = 0

        class FlakyTTSTool(BaseTool):
            name = "flaky_tts"
            description = "Fails on first call"
            parameters = {}

            async def execute(self, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ToolExecutionError("Service unavailable")
                return ToolResult.ok({"url": "https://tts.example/ok.mp3"})

        tool_registry.register(FlakyTTSTool())
        try:
            provider = TTSProvider(tool_name="flaky_tts")
            result = await provider.synthesize("First. Second.", max_len=200)
            assert len(result) == 1  # first seg failed, second succeeded
            assert result[0] == "https://tts.example/ok.mp3"
            assert call_count == 2
        finally:
            tool_registry.reset()

    @pytest.mark.asyncio
    async def test_returns_empty_when_tool_times_out(self, monkeypatch) -> None:
        from backend.tools.base import BaseTool, ToolResult, tool_registry

        monkeypatch.setattr(settings, "tts_enabled", True)

        class SlowTTSTool(BaseTool):
            name = "slow_tts"
            description = "Always times out"
            parameters = {}

            async def execute(self, **kwargs):
                import asyncio
                await asyncio.sleep(10)  # longer than _TTS_TIMEOUT
                return ToolResult.ok({"url": "https://tts.example/too-late.mp3"})

        tool_registry.register(SlowTTSTool())
        try:
            provider = TTSProvider(tool_name="slow_tts")
            result = await provider.synthesize("Hello.", max_len=200)
            assert result == []  # timeout → empty
        finally:
            tool_registry.reset()
