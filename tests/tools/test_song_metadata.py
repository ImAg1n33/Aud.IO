"""歌曲元数据透传（RFC-020 契约 A）—— 封面/专辑/时长。"""

import pytest

from backend.tools import netease_api
from backend.tools.netease_api import (
    _extract_cover_album,
    _extract_duration_ms,
    find_similar_songs,
    search_first_song,
)


class TestExtractors:
    def test_cloudsearch_shape(self) -> None:
        song = {"al": {"name": "叶惠美", "picUrl": "http://c/1.jpg"}, "dt": 269000}
        assert _extract_cover_album(song) == ("http://c/1.jpg", "叶惠美")
        assert _extract_duration_ms(song) == 269000

    def test_search_shape(self) -> None:
        song = {
            "album": {"name": "Album", "picUrl": "http://c/2.jpg"},
            "duration": 180000,
        }
        assert _extract_cover_album(song) == ("http://c/2.jpg", "Album")
        assert _extract_duration_ms(song) == 180000

    def test_missing_fields(self) -> None:
        assert _extract_cover_album({}) == ("", "")
        assert _extract_duration_ms({}) is None
        assert _extract_duration_ms({"dt": 0}) is None


class TestPassthrough:
    @pytest.mark.asyncio
    async def test_search_first_song_includes_metadata(self, monkeypatch) -> None:
        payload = {
            "result": {
                "songs": [
                    {
                        "id": 1,
                        "name": "晴天",
                        "ar": [{"name": "周杰伦"}],
                        "originCoverType": 1,
                        "pop": 100,
                        "al": {"name": "叶惠美", "picUrl": "http://c/1.jpg"},
                        "dt": 269000,
                    }
                ]
            }
        }

        async def fake_request(path, params, timeout=15):
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        song = await search_first_song("周杰伦 晴天")
        assert song["cover_url"] == "http://c/1.jpg"
        assert song["album"] == "叶惠美"
        assert song["duration_ms"] == 269000

    @pytest.mark.asyncio
    async def test_find_similar_includes_metadata(self, monkeypatch) -> None:
        payload = {
            "songs": [
                {
                    "id": 2,
                    "name": "兄弟",
                    "ar": [{"name": "周杰伦"}],
                    "popularity": 90,
                    "al": {"name": "A", "picUrl": "http://c/2.jpg"},
                    "dt": 200000,
                }
            ]
        }

        async def fake_request(path, params, timeout=15):
            return payload

        monkeypatch.setattr(netease_api, "_request_json", fake_request)
        songs = await find_similar_songs("1")
        assert songs[0]["cover_url"] == "http://c/2.jpg"
        assert songs[0]["duration_ms"] == 200000
