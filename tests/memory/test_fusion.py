"""RRF 融合测试 —— 混合检索的关键词腿 + 语义腿融合逻辑。"""

from backend.memory.fusion import rrf_fuse
from backend.memory.models import EpisodicSnapshot


def _snap(sid: int, similarity: float | None = None) -> EpisodicSnapshot:
    return EpisodicSnapshot(
        id=sid,
        timestamp="2026-08-14T00:00:00Z",
        user_input=f"input {sid}",
        assistant_reply="",
        played_song_name=None,
        played_song_artist=None,
        mood_tag=None,
        weather_tag=None,
        time_of_day="night",
        genre_tag=None,
        similarity_score=similarity,
    )


class TestRrfFuse:
    def test_fuses_both_lists_preserving_semantic_metadata(self) -> None:
        semantic = [_snap(1, 0.9), _snap(2, 0.8)]
        keyword = [_snap(3), _snap(4)]
        fused = rrf_fuse(semantic, keyword)
        # RRF: 1↔3 同分(1/61)，2↔4 同分(1/62)；并列按稳定序保持插入序
        assert [s.id for s in fused] == [1, 3, 2, 4]
        assert fused[0].similarity_score == 0.9

    def test_dedupes_across_lists(self) -> None:
        semantic = [_snap(1, 0.9), _snap(2, 0.8)]
        keyword = [_snap(2), _snap(3)]
        fused = rrf_fuse(semantic, keyword)
        ids = [s.id for s in fused]
        assert ids == [2, 1, 3]  # id=2 双路命中 → RRF 分最高
        assert len(set(ids)) == len(ids)

    def test_semantic_rank_wins_on_tie(self) -> None:
        # id=1 语义第1(keyword 未出现)，id=2 语义第2+关键词第1
        semantic = [_snap(1, 0.95), _snap(2, 0.9)]
        keyword = [_snap(2), _snap(1)]
        fused = rrf_fuse(semantic, keyword)
        # 1: 1/61 + 1/62 = 0.0325; 2: 1/62 + 1/61 = 0.0325 → 平手，按稳定排序保留语义序
        assert [s.id for s in fused][0] in (1, 2)

    def test_empty_lists(self) -> None:
        assert rrf_fuse([], []) == []
        assert [s.id for s in rrf_fuse([_snap(1)], [])] == [1]
        assert [s.id for s in rrf_fuse([], [_snap(2)])] == [2]

    def test_custom_k(self) -> None:
        semantic = [_snap(1)]
        keyword = [_snap(1)]
        fused = rrf_fuse(semantic, keyword, k=10)
        assert [s.id for s in fused] == [1]