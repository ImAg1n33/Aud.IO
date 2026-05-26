import pytest

from backend.tools.base import MusicCopyrightError, MusicSearchError, ToolExecutionError, tool_registry
from backend.tools.music_tool import GetMusicUrlTool, SearchMusicTool
from backend.tools.netease_api import CookieExpiredError


@pytest.fixture
def ensure_music_tools() -> None:
    """Re-register music tools in case another test module cleared the registry."""
    tool_registry.register(SearchMusicTool())
    tool_registry.register(GetMusicUrlTool())
    yield


class TestSearchMusicTool:
    @pytest.mark.asyncio
    async def test_execute_success(self, monkeypatch) -> None:
        async def fake_search(keyword: str):
            return {"id": "123", "name": "Test Song", "artist": "Test Artist"}

        tool = SearchMusicTool()
        monkeypatch.setattr(
            "backend.tools.music_tool.search_first_song", fake_search
        )

        result = await tool.execute(keyword="Test")
        assert result.success is True
        assert result.data["song_id"] == "123"
        assert result.data["name"] == "Test Song"
        assert result.data["artist"] == "Test Artist"

    @pytest.mark.asyncio
    async def test_execute_failure(self, monkeypatch) -> None:
        async def fake_search(keyword: str):
            raise RuntimeError("API down")

        tool = SearchMusicTool()
        monkeypatch.setattr(
            "backend.tools.music_tool.search_first_song", fake_search
        )

        result = await tool.execute(keyword="Test")
        assert result.success is False
        assert isinstance(result.error, MusicSearchError)

    @pytest.mark.asyncio
    async def test_execute_empty_keyword(self) -> None:
        tool = SearchMusicTool()
        result = await tool.execute(keyword="")
        assert result.success is False

    def test_registered(self, ensure_music_tools) -> None:
        assert "search_music" in tool_registry


class TestGetMusicUrlTool:
    @pytest.mark.asyncio
    async def test_execute_success(self, monkeypatch) -> None:
        async def fake_get_url(song_id: str, level: str = "standard"):
            return "https://example.com/song.mp3"

        tool = GetMusicUrlTool()
        monkeypatch.setattr(
            "backend.tools.music_tool.get_song_mp3_url", fake_get_url
        )

        result = await tool.execute(song_id="123")
        assert result.success is True
        assert result.data["mp3_url"] == "https://example.com/song.mp3"

    @pytest.mark.asyncio
    async def test_execute_copyright_failure(self, monkeypatch) -> None:
        async def fake_get_url(song_id: str, level: str = "standard"):
            raise LookupError("No playable url found for song_id: 123")

        tool = GetMusicUrlTool()
        monkeypatch.setattr(
            "backend.tools.music_tool.get_song_mp3_url", fake_get_url
        )

        result = await tool.execute(song_id="123")
        assert result.success is False
        assert isinstance(result.error, MusicCopyrightError)

    @pytest.mark.asyncio
    async def test_execute_empty_song_id(self) -> None:
        tool = GetMusicUrlTool()
        result = await tool.execute(song_id="")
        assert result.success is False

    def test_registered(self, ensure_music_tools) -> None:
        assert "get_music_url" in tool_registry


class TestCookieExpiredHandling:
    """Verify CookieExpiredError is caught and surfaced as a tool error."""

    @pytest.mark.asyncio
    async def test_search_returns_error_on_expired_cookie(self, monkeypatch) -> None:
        async def fake_search(keyword: str):
            raise CookieExpiredError("Cookie expired — re-login required")

        monkeypatch.setattr(
            "backend.tools.music_tool.search_first_song", fake_search,
        )
        tool = SearchMusicTool()
        result = await tool.execute(keyword="test")
        assert result.success is False
        assert isinstance(result.error, MusicSearchError)
        assert "Cookie expired" in str(result.error)

    @pytest.mark.asyncio
    async def test_get_url_returns_error_on_expired_cookie(self, monkeypatch) -> None:
        async def fake_get_url(song_id: str, level: str = "standard"):
            raise CookieExpiredError("Cookie expired")

        monkeypatch.setattr(
            "backend.tools.music_tool.get_song_mp3_url", fake_get_url,
        )
        tool = GetMusicUrlTool()
        result = await tool.execute(song_id="123")
        assert result.success is False
        assert isinstance(result.error, ToolExecutionError)
        assert "Cookie expired" in str(result.error)
