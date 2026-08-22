"""会话摘要 Provider + 反射触发测试。"""

import pytest

from backend.agent.context_assembler import ContextAssembler, SessionSummaryProvider
from backend.agent.intent_classifier import Intent
from backend.memory.episodic_memory import EpisodicMemory


@pytest.fixture
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(db_path=tmp_path / "episodes.db")


class TestSessionSummaryProvider:
    @pytest.mark.asyncio
    async def test_injects_summaries_for_session(self, episodic) -> None:
        await episodic.insert_session_summary(
            "alice", "用户喜欢深夜爵士。", ["爵士"], [], turn_count=10,
        )
        await episodic.insert_session_summary(
            "alice", "用户在学吉他。", ["吉他"], [], turn_count=20,
        )

        provider = SessionSummaryProvider(episodic, limit=3)
        block = await provider.get_context(
            Intent.MUSIC_RECOMMEND, "推荐点歌", {"_session_id": "alice"},
        )
        assert block is not None
        assert "深夜爵士" in block
        assert "学吉他" in block
        assert "Previous sessions" in block

    @pytest.mark.asyncio
    async def test_no_session_id_returns_none(self, episodic) -> None:
        provider = SessionSummaryProvider(episodic)
        assert await provider.get_context(Intent.CHITCHAT, "hi", {}) is None

    @pytest.mark.asyncio
    async def test_no_summaries_returns_none(self, episodic) -> None:
        provider = SessionSummaryProvider(episodic)
        block = await provider.get_context(
            Intent.CHITCHAT, "hi", {"_session_id": "nobody"},
        )
        assert block is None

    @pytest.mark.asyncio
    async def test_session_isolation(self, episodic) -> None:
        await episodic.insert_session_summary("alice", "alice 的摘要", [], [], turn_count=5)
        provider = SessionSummaryProvider(episodic)
        assert await provider.get_context(
            Intent.CHITCHAT, "hi", {"_session_id": "bob"},
        ) is None


class TestAssemblerIncludesSummaryProvider:
    @pytest.mark.asyncio
    async def test_assembler_includes_session_summaries(self, episodic) -> None:
        await episodic.insert_session_summary(
            "s1", "用户喜欢日系 city pop。", ["city pop"], [], turn_count=10,
        )
        assembler = ContextAssembler(providers=[SessionSummaryProvider(episodic)])
        prompt = await assembler.assemble(
            "推荐点歌", Intent.MUSIC_RECOMMEND, session_id="s1",
        )
        assert "city pop" in prompt