import pytest

from backend.agent.context_assembler import (
    ContextAssembler,
    ConversationHistoryProvider,
    CurrentlyPlayingProvider,
    EpisodicMemoryProvider,
    ToolSchemaProvider,
    UserPreferenceProvider,
)
from backend.agent.intent_classifier import Intent
from backend.agent.prompt_builder import ENHANCED_SYSTEM_PERSONA, ENHANCED_TOOL_CONSTRAINTS
from backend.memory.conversation_memory import ConversationMemory
from backend.memory.episodic_memory import EpisodicMemory


class StubMemoryManager:
    def get_preference_summary(self) -> str:
        return "Preferred genres: jazz, pop."


@pytest.fixture
def stub_memory_manager():
    return StubMemoryManager()


@pytest.fixture
def conversation_memory():
    cm = ConversationMemory(max_turns=5)
    cm.add_turn("play jazz", "Playing Miles Davis.", intent="music_play")
    return cm


@pytest.fixture
def episodic_memory(tmp_path):
    return EpisodicMemory(db_path=tmp_path / "test_episodes.db")


@pytest.fixture
def assembler(conversation_memory, stub_memory_manager, episodic_memory):
    return ContextAssembler(
        providers=[
            ConversationHistoryProvider(conversation_memory),
            UserPreferenceProvider(stub_memory_manager),
            CurrentlyPlayingProvider(),
            ToolSchemaProvider(),
            EpisodicMemoryProvider(episodic_memory),
        ],
        system_persona=ENHANCED_SYSTEM_PERSONA,
        tool_constraints=ENHANCED_TOOL_CONSTRAINTS,
    )


class TestConversationHistoryProvider:
    @pytest.mark.asyncio
    async def test_returns_history_when_present(self, conversation_memory) -> None:
        provider = ConversationHistoryProvider(conversation_memory)
        result = await provider.get_context(Intent.MUSIC_PLAY, "hello", {})
        assert result is not None
        assert "play jazz" in result
        assert "Miles Davis" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self) -> None:
        provider = ConversationHistoryProvider(ConversationMemory())
        result = await provider.get_context(Intent.MUSIC_PLAY, "hello", {})
        assert result is None


class TestUserPreferenceProvider:
    @pytest.mark.asyncio
    async def test_returns_profile_for_music_intent(self, stub_memory_manager) -> None:
        provider = UserPreferenceProvider(stub_memory_manager)
        result = await provider.get_context(Intent.MUSIC_PLAY, "play jazz", {})
        assert result is not None
        assert "jazz" in result
        assert "How to use this profile" in result

    @pytest.mark.asyncio
    async def test_returns_none_for_chitchat(self, stub_memory_manager) -> None:
        provider = UserPreferenceProvider(stub_memory_manager)
        result = await provider.get_context(Intent.CHITCHAT, "hello", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_weather(self, stub_memory_manager) -> None:
        provider = UserPreferenceProvider(stub_memory_manager)
        result = await provider.get_context(Intent.WEATHER, "天气", {})
        assert result is None


class TestCurrentlyPlayingProvider:
    @pytest.mark.asyncio
    async def test_returns_now_playing(self) -> None:
        provider = CurrentlyPlayingProvider()
        result = await provider.get_context(Intent.MUSIC_PLAY, "", {"Currently Playing": "Artist - Song"})
        assert result is not None
        assert "Artist - Song" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_none(self) -> None:
        provider = CurrentlyPlayingProvider()
        result = await provider.get_context(Intent.MUSIC_PLAY, "", {"Currently Playing": "None"})
        assert result is None


from backend.tools.base import BaseTool, ToolResult, tool_registry


class _TempTool(BaseTool):
    name = "test_temp_tool"
    description = "Temporary tool for testing ToolSchemaProvider."
    parameters = {}

    async def execute(self, **kwargs):
        return ToolResult.ok()


class TestToolSchemaProvider:
    @pytest.mark.asyncio
    async def test_returns_tool_list(self) -> None:
        tool_registry.register(_TempTool())
        try:
            provider = ToolSchemaProvider()
            result = await provider.get_context(Intent.MUSIC_PLAY, "", {})
            assert result is not None
            assert "test_temp_tool" in result
        finally:
            tool_registry.reset()


class TestEpisodicMemoryProvider:
    @pytest.mark.asyncio
    async def test_returns_none_for_chitchat(self, episodic_memory) -> None:
        provider = EpisodicMemoryProvider(episodic_memory)
        result = await provider.get_context(Intent.CHITCHAT, "hello", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_temporal_signal(self, episodic_memory) -> None:
        provider = EpisodicMemoryProvider(episodic_memory)
        result = await provider.get_context(Intent.MUSIC_PLAY, "play jazz", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_queries_when_temporal_signal(self, episodic_memory) -> None:
        await episodic_memory.store_snapshot(
            "play jazz", "Playing jazz.", played_song={"name": "So What", "artist": "Miles Davis"}
        )
        provider = EpisodicMemoryProvider(episodic_memory)
        result = await provider.get_context(Intent.MUSIC_PLAY, "上次那首", {})
        assert result is not None
        assert "So What" in result


class TestContextAssembler:
    @pytest.mark.asyncio
    async def test_assembles_full_prompt(self, assembler) -> None:
        prompt = await assembler.assemble(
            user_input="播放一首爵士乐",
            intent=Intent.MUSIC_PLAY,
            metadata={"Currently Playing": "None"},
        )
        assert "播放一首爵士乐" in prompt
        assert ENHANCED_SYSTEM_PERSONA in prompt
        assert ENHANCED_TOOL_CONSTRAINTS in prompt
        assert "jazz" in prompt  # from preference

    @pytest.mark.asyncio
    async def test_chitchat_skips_preferences(self, assembler) -> None:
        prompt = await assembler.assemble(
            user_input="你好",
            intent=Intent.CHITCHAT,
            metadata={},
        )
        assert "你好" in prompt
        # Should NOT include profile instructions for chitchat
        assert "How to use this profile" not in prompt
