"""搜索质量评分测试（v0.7 挑歌精度）—— RyaVocal 翻唱案例回归。"""

import pytest

from backend.tools import netease_api
from backend.tools.netease_api import (
    NoAcceptableSongError,
    _score_candidate,
    find_similar_songs,
    search_first_song,
)


def _song(**kwargs):
    base = {"id": 1, "name": "晴天", "ar": [{"name": "周杰伦"}], "pop": 100}
    base.update(kwargs)
    return base


class TestScoreCandidate:
    def test_cover_penalized(self) -> None:
        cover = _song(
            name="晴天 (原唱 周杰伦)", ar=[{"name": "RyaVocal"}],
            originCoverType=2,
        )
        assert _score_candidate(cover, "周杰伦 晴天") <= 0

    def test_official_boosted(self) -> None:
        official = _song(originCoverType=1)
        assert _score_candidate(official, "周杰伦 晴天") > 3.0

    def test_impersonator_artist_not_matched(self) -> None:
        fake = _song(ar=[{"name": "周杰伦."}], originCoverType=0)
        real = _song(ar=[{"name": "周杰伦"}], originCoverType=0)
        assert _score_candidate(fake, "周杰伦 晴天") < _score_candidate(
            real, "周杰伦 晴天"
        )

    def test_popularity_bonus(self) -> None:
        low = _song(pop=10, originCoverType=0)
        high = _song(pop=100, originCoverType=0)
        assert _score_candidate(high, "周杰伦 晴天") > _score_candidate(
            low, "周杰伦 晴天"
        )

    def test_low_quality_marker_penalty(self) -> None:
        piano = _song(name="晴天 (钢琴版) [原唱: 周杰伦]", ar=[{"name": "纪钧瀚"}])
        assert _score_candidate(piano, "周杰伦 晴天") <= 0


class TestSearchFirstSong:
    @pytest.mark.asyncio
    async def test_prefers_official_over_cover(self, monkeypatch) -> None:
        """RyaVocal 案例回归：官方版排在翻唱前/后被正确选中。"""
        payload = {
            "result": {
                "songs": [
                    {
                        "id": 2668397359,
                        "name": "晴天 (原唱 周杰伦)",
                        "ar": [{"name": "RyaVocal"}],
                        "pop": 100,
                        "originCoverType": 2,
                    },
                    {
                        "id": 186016,
                        "name": "晴天",
                        "ar": [{"name": "周杰伦"}],
                        "pop": 100,
                        "originCoverType": 1,
                    },
                ]
            }
        }

        async def fake_request(path, params):
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        song = await search_first_song("周杰伦 晴天")
        assert song["id"] == "186016"
        assert song["artist"] == "周杰伦"

    @pytest.mark.asyncio
    async def test_all_covers_rejected(self, monkeypatch) -> None:
        """全是翻唱 → NoAcceptableSongError（换曲信号，不静默播翻唱）。"""
        payload = {
            "result": {
                "songs": [
                    {
                        "id": 1, "name": "晴天 (原唱 周杰伦)",
                        "ar": [{"name": "RyaVocal"}], "pop": 100,
                        "originCoverType": 2,
                    },
                    {
                        "id": 2, "name": "晴天 (女声版)",
                        "ar": [{"name": "GYBeat"}], "pop": 100,
                        "originCoverType": 2,
                    },
                ]
            }
        }

        async def fake_request(path, params):
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        with pytest.raises(NoAcceptableSongError):
            await search_first_song("周杰伦 晴天")

    @pytest.mark.asyncio
    async def test_falls_back_to_search_endpoint(self, monkeypatch) -> None:
        calls: list[str] = []

        async def fake_request(path, params):
            calls.append(path)
            if path == "cloudsearch":
                raise RuntimeError("cloudsearch down")
            return {
                "result": {
                    "songs": [
                        {
                            "id": 9, "name": "爱爱爱",
                            "ar": [{"name": "方大同"}], "pop": 100,
                            "originCoverType": 1,
                        }
                    ]
                }
            }

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        song = await search_first_song("方大同 爱爱爱")
        assert song["id"] == "9"
        assert calls == ["cloudsearch", "search"]

    @pytest.mark.asyncio
    async def test_skips_unavailable(self, monkeypatch) -> None:
        payload = {
            "result": {
                "songs": [
                    {
                        "id": 1, "name": "爱爱爱", "ar": [{"name": "方大同"}],
                        "st": -1, "pop": 100, "originCoverType": 1,
                    },
                    {
                        "id": 2, "name": "爱爱爱 (Acoustic Version)",
                        "ar": [{"name": "方大同"}], "pop": 95,
                        "originCoverType": 1,
                    },
                ]
            }
        }

        async def fake_request(path, params):
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        song = await search_first_song("方大同 爱爱爱")
        assert song["id"] == "2"


class TestFindSimilar:
    @pytest.mark.asyncio
    async def test_filters_covers_and_sorts_by_popularity(self, monkeypatch) -> None:
        payload = {
            "songs": [
                {"id": "a", "name": "Song A", "ar": [{"name": "X"}],
                 "popularity": 50, "originCoverType": 2},          # 翻唱 → 过滤
                {"id": "b", "name": "Song B", "ar": [{"name": "Y"}],
                 "popularity": 80, "originCoverType": 1},
                {"id": "c", "name": "Song C (钢琴版)", "ar": [{"name": "Z"}],
                 "popularity": 90, "originCoverType": 1},          # 劣质标记 → 过滤
                {"id": "d", "name": "Song D", "ar": [{"name": "W"}],
                 "popularity": 60, "originCoverType": 1},
                {"id": "seed", "name": "Seed", "ar": [{"name": "S"}],
                 "popularity": 100, "originCoverType": 1},         # 自身 → 过滤
            ]
        }

        async def fake_request(path, params):
            assert path == "simi/song"
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        result = await find_similar_songs("seed")
        assert [r["id"] for r in result] == ["b", "d"]

    @pytest.mark.asyncio
    async def test_find_similar_tool_modes(self, monkeypatch) -> None:
        candidates = [
            {"id": "1", "name": "A", "artist": "X", "popularity": 90},
            {"id": "2", "name": "B", "artist": "Y", "popularity": 80},
        ]

        async def fake_find(song_id, limit=10):
            return candidates

        monkeypatch.setattr(
            "backend.tools.music_tool.find_similar_songs", fake_find
        )
        from backend.tools.music_tool import FindSimilarTool

        tool = FindSimilarTool()
        similar = await tool.execute(song_id="seed", mode="similar")
        assert similar.success
        assert similar.data["song_id"] == "1"

        explore = await tool.execute(song_id="seed", mode="explore")
        assert explore.success
        assert explore.data["song_id"] == "2"