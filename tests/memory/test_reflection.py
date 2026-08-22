"""Reflection 会话摘要测试 —— 跨会话连续性（v5）。"""

import pytest

from backend.memory.episodic_memory import EpisodicMemory
from backend.memory.reflection import SessionReflector


@pytest.fixture
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(db_path=tmp_path / "episodes.db")


class TestSummaryStorage:
    @pytest.mark.asyncio
    async def test_migration_v5_table_exists(self, episodic, tmp_path) -> None:
        import sqlite3

        with sqlite3.connect(str(tmp_path / "episodes.db")) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='session_summaries'"
            ).fetchone()
            assert row is not None

    @pytest.mark.asyncio
    async def test_insert_and_query_roundtrip(self, episodic) -> None:
        summary_id = await episodic.insert_session_summary(
            session_id="alice",
            summary_text="用户喜欢深夜听爵士。",
            topics=["爵士", "深夜"],
            song_signals=[{"song": "Miles Davis", "signal": "liked"}],
            turn_count=12,
        )
        assert isinstance(summary_id, int)

        summaries = await episodic.query_recent_summaries("alice")
        assert len(summaries) == 1
        assert summaries[0]["summary_text"] == "用户喜欢深夜听爵士。"
        assert summaries[0]["topics"] == ["爵士", "深夜"]
        assert summaries[0]["song_signals"] == [{"song": "Miles Davis", "signal": "liked"}]
        assert summaries[0]["turn_count"] == 12

    @pytest.mark.asyncio
    async def test_recent_limit_and_session_isolation(self, episodic) -> None:
        for i in range(4):
            await episodic.insert_session_summary("alice", f"摘要 {i}", [], [], turn_count=i + 10)
        await episodic.insert_session_summary("bob", "bob 的摘要", [], [], turn_count=1)

        recent = await episodic.query_recent_summaries("alice", limit=3)
        assert len(recent) == 3
        assert recent[0]["summary_text"] == "摘要 3"  # 新→旧

        bob = await episodic.query_recent_summaries("bob")
        assert len(bob) == 1
        assert "bob" in bob[0]["summary_text"]


class TestSessionReflector:
    @pytest.mark.asyncio
    async def test_summarize_parses_llm_output(self, episodic, monkeypatch) -> None:
        async def fake_request(messages, model=None, temperature=0.1):
            return {
                "summary": "用户喜欢深夜爵士，反感喧闹。",
                "topics": ["爵士", "深夜"],
                "song_signals": [{"song": "So What", "signal": "liked"}],
            }

        monkeypatch.setattr("backend.memory.reflection.request_json_object", fake_request)
        reflector = SessionReflector(episodic)
        result = await reflector.summarize("transcript...", turn_count=12)

        assert result is not None
        assert "爵士" in result["summary"]
        assert result["topics"] == ["爵士", "深夜"]
        assert result["song_signals"][0]["signal"] == "liked"

    @pytest.mark.asyncio
    async def test_summarize_cleans_invalid_signal(self, episodic, monkeypatch) -> None:
        async def fake_request(messages, model=None, temperature=0.1):
            return {
                "summary": "有效摘要。",
                "topics": ["a", 123, ""],
                "song_signals": [
                    {"song": "X", "signal": "loved"},  # 非法信号 → 丢弃
                    {"song": "Y", "signal": "disliked"},
                    "garbage",
                ],
            }

        monkeypatch.setattr("backend.memory.reflection.request_json_object", fake_request)
        reflector = SessionReflector(episodic)
        result = await reflector.summarize("transcript", turn_count=1)

        assert result["topics"] == ["a", "123"]
        assert result["song_signals"] == [{"song": "Y", "signal": "disliked"}]

    @pytest.mark.asyncio
    async def test_summarize_empty_summary_returns_none(self, episodic, monkeypatch) -> None:
        async def fake_request(messages, model=None, temperature=0.1):
            return {"summary": "", "topics": [], "song_signals": []}

        monkeypatch.setattr("backend.memory.reflection.request_json_object", fake_request)
        reflector = SessionReflector(episodic)
        assert await reflector.summarize("transcript", turn_count=1) is None

    @pytest.mark.asyncio
    async def test_summarize_llm_failure_silent(self, episodic, monkeypatch) -> None:
        async def fake_request(messages, model=None, temperature=0.1):
            raise RuntimeError("llm down")

        monkeypatch.setattr("backend.memory.reflection.request_json_object", fake_request)
        reflector = SessionReflector(episodic)
        assert await reflector.summarize("transcript", turn_count=1) is None

    @pytest.mark.asyncio
    async def test_summarize_and_store_persists(self, episodic, monkeypatch) -> None:
        async def fake_request(messages, model=None, temperature=0.1):
            return {"summary": "跨会话摘要。", "topics": ["记忆"], "song_signals": []}

        monkeypatch.setattr("backend.memory.reflection.request_json_object", fake_request)
        reflector = SessionReflector(episodic)
        summary_id = await reflector.summarize_and_store("alice", "transcript", turn_count=12)
        assert summary_id is not None

        summaries = await episodic.query_recent_summaries("alice")
        assert summaries[0]["summary_text"] == "跨会话摘要。"
        assert summaries[0]["turn_count"] == 12