"""混合检索测试 —— query_by_semantic 的语义腿 + 关键词腿 + 降级。"""

import pytest

from backend.memory.episodic_memory import EpisodicMemory


@pytest.fixture
def episodic(tmp_path) -> EpisodicMemory:
    return EpisodicMemory(db_path=tmp_path / "episodes.db")


class TestHybridKeywordLeg:
    @pytest.mark.asyncio
    async def test_exact_substring_recalled_by_keyword_leg(self, episodic) -> None:
        """原文复述类查询：关键词腿兜住（语义腿可能漏）。"""
        await episodic.store_snapshot(
            "来一首周杰伦的晴天", "好的，周杰伦《晴天》。",
            played_song={"song_id": "1001", "name": "晴天", "artist": "周杰伦"},
        )
        await episodic.store_snapshot(
            "推荐点爵士", "Miles Davis 走起。",
            played_song={"song_id": "1002", "name": "So What", "artist": "Miles Davis"},
        )
        # 关键词腿: "晴天" 命中第一条的 user_input 与 song_name
        results = await episodic.query_by_semantic("晴天", limit=5)
        ids = [s.id for s in results]
        assert 1 in ids

    @pytest.mark.asyncio
    async def test_song_artist_substring_recalled(self, episodic) -> None:
        """歌名/艺人子串命中（play song 字段参与 LIKE）。"""
        await episodic.store_snapshot(
            "放点好听的", "来了。", played_song={"song_id": "2001", "name": "Tadow", "artist": "FKJ"},
        )
        results = await episodic.query_by_semantic("FKJ 那首", limit=5)
        assert 1 in [s.id for s in results]

    @pytest.mark.asyncio
    async def test_session_filter_applies_to_keyword_leg(self, episodic) -> None:
        await episodic.store_snapshot(
            "来一首晴天", "好。", played_song={"song_id": "3001", "name": "晴天", "artist": "周杰伦"},
            session_id="alice",
        )
        await episodic.store_snapshot(
            "来一首晴天", "好。", played_song={"song_id": "3002", "name": "晴天", "artist": "周杰伦"},
            session_id="bob",
        )
        results = await episodic.query_by_semantic("晴天", session_id="alice", limit=5)
        ids = [s.id for s in results]
        assert 1 in ids
        assert 2 not in ids

    @pytest.mark.asyncio
    async def test_hybrid_recalls_both_legs_without_duplicates(self, episodic) -> None:
        """同一快照被两路同时命中 → 去重（RRF 融合）。"""
        await episodic.store_snapshot(
            "深夜听点爵士", "Miles Davis 的 So What。",
            played_song={"song_id": "4001", "name": "So What", "artist": "Miles Davis"},
        )
        results = await episodic.query_by_semantic("爵士 So What", limit=5)
        ids = [s.id for s in results]
        assert len(ids) == len(set(ids))
        assert 1 in ids


class TestHybridFallback:
    @pytest.mark.asyncio
    async def test_chroma_down_falls_back_to_keyword(self, episodic, monkeypatch) -> None:
        """ChromaDB 挂 → 纯 SQLite 关键词降级（多级降级原则）。"""
        await episodic.store_snapshot("来一首周杰伦的晴天", "好的。")
        await episodic.store_snapshot("推荐点爵士", "Miles Davis。")

        async def boom(query_text, where, limit):
            raise RuntimeError("chroma down")

        monkeypatch.setattr(episodic._chroma, "semantic_search", boom)
        results = await episodic.query_by_semantic("晴天", limit=5)
        assert 1 in [s.id for s in results]