import json
from unittest.mock import AsyncMock, Mock

import pytest

import backend.agent.memory_manager as memory_manager_module
from backend.agent.memory_manager import MemoryManager


class FakePatchMemoryManager(MemoryManager):
    async def _request_patch_from_model(self, user_input, assistant_reply, old_profile):
        return [
            {"op": "add", "path": "/core_taste/-", "value": "jazz"},
            {"op": "add", "path": "/artist_preference/liked/-", "value": "Miles Davis"},
        ]


class FakeAuditMemoryManager(MemoryManager):
    async def _request_patch_from_model(self, user_input, assistant_reply, old_profile):
        return [{"op": "add", "path": "/artist_preference/liked/-", "value": "陈奕迅"}]


class FakeNoChangeMemoryManager(MemoryManager):
    async def _request_patch_from_model(self, user_input, assistant_reply, old_profile):
        return []


@pytest.mark.asyncio
async def test_audit_log_records_patch_content(tmp_path, monkeypatch) -> None:
    """审计日志记录 patch 内容摘要 —— '这条偏好怎么进来的' 可追踪。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))

    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "core_taste": [],
                "artist_preference": {"liked": [], "disliked": []},
                "mood_bias": {},
                "last_updated": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = FakeAuditMemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    await manager.async_update_profile("陈奕迅的歌太好听了", "记下了")

    log = tmp_path / "memory_update.log"
    assert log.exists()
    last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["status"] == "updated"
    assert last["patch_summary"][0]["path"] == "/artist_preference/liked/-"
    assert "陈奕迅" in last["patch_summary"][0]["value"]


@pytest.mark.asyncio
async def test_async_update_profile_applies_patch(tmp_path) -> None:
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "core_taste": ["pop"],
                "artist_preference": {"liked": ["A"], "disliked": []},
                "mood_bias": {},
                "last_updated": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = FakePatchMemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("我最近喜欢爵士", "好的，记录了")

    assert "jazz" in updated["core_taste"]
    assert "Miles Davis" in updated["artist_preference"]["liked"]

    persisted = json.loads(profile_path.read_text(encoding="utf-8"))
    assert "jazz" in persisted["core_taste"]


@pytest.mark.asyncio
async def test_async_update_profile_no_change_keeps_profile(tmp_path) -> None:
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "core_taste": ["pop"],
                "artist_preference": {"liked": ["A"], "disliked": []},
                "mood_bias": {},
                "last_updated": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = FakeNoChangeMemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("无偏好变化", "了解")

    assert updated["core_taste"] == ["pop"]
    assert updated["artist_preference"]["liked"] == ["A"]


@pytest.mark.asyncio
async def test_async_update_profile_uses_deepseek_reasoner_model(tmp_path, monkeypatch) -> None:
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "core_taste": ["pop"],
                "artist_preference": {"liked": ["A"], "disliked": []},
                "mood_bias": {},
                "last_updated": "2026-01-01T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    mocked_request = AsyncMock(
        return_value={
            "patch": [
                {"op": "add", "path": "/core_taste/-", "value": "jazz"},
            ]
        }
    )
    monkeypatch.setattr(memory_manager_module, "SLOW_CRITIC_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(memory_manager_module, "request_json_object", mocked_request)

    manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("我最近更喜欢爵士", "收到，我会按这个方向推荐")

    mocked_request.assert_called_once()
    _, kwargs = mocked_request.call_args
    assert kwargs["model"] == "deepseek-v4-pro"
    assert kwargs["temperature"] == 0.1
    assert isinstance(kwargs["messages"], list)
    assert "jazz" in updated["core_taste"]


@pytest.mark.asyncio
async def test_async_update_profile_empty_patch_does_not_overwrite_profile_file(tmp_path, monkeypatch) -> None:
    profile_path = tmp_path / "user_profile.json"
    original_profile = {
        "core_taste": ["pop"],
        "artist_preference": {"liked": ["A"], "disliked": []},
        "mood_bias": {},
        "last_updated": "2026-01-01T00:00:00Z",
    }
    original_text = json.dumps(original_profile, ensure_ascii=False, indent=2) + "\n"
    profile_path.write_text(original_text, encoding="utf-8")

    mocked_request = AsyncMock(return_value={})
    monkeypatch.setattr(memory_manager_module, "request_json_object", mocked_request)

    manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("这次没有新增偏好", "明白，不更新记忆")

    mocked_request.assert_called_once()

    persisted_text = profile_path.read_text(encoding="utf-8")
    assert persisted_text == original_text
    assert persisted_text.strip() != ""
    assert json.loads(persisted_text) == original_profile
    assert updated == original_profile


class TestPreferenceSummary:
    def test_summary_includes_core_taste(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": ["jazz", "pop"],
                    "artist_preference": {"liked": [], "disliked": []},
                    "mood_bias": {},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        summary = manager.get_preference_summary()
        assert "jazz, pop" in summary
        assert "Preferred genres" in summary

    def test_summary_includes_artist_constraints(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": ["A"], "disliked": ["B"]},
                    "mood_bias": {},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        summary = manager.get_preference_summary()
        assert "A" in summary
        assert "Avoid these artists" in summary

    def test_summary_includes_mood_bias(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": [], "disliked": []},
                    "mood_bias": {"happy": ["pop"], "sad": ["ballad"]},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        summary = manager.get_preference_summary()
        assert "happy → pop" in summary
        assert "sad → ballad" in summary

    def test_summary_empty_profile(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        summary = manager.get_preference_summary()
        assert "No music preferences" in summary


class TestMoodRecommendations:
    def test_returns_genres_for_known_mood(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": [], "disliked": []},
                    "mood_bias": {"focused": ["lofi", "ambient"], "calm": ["jazz"]},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        assert manager.get_mood_recommendations("focused") == ["lofi", "ambient"]
        assert manager.get_mood_recommendations("calm") == ["jazz"]

    def test_case_insensitive(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": [], "disliked": []},
                    "mood_bias": {"Happy": ["pop"]},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        assert manager.get_mood_recommendations("HAPPY") == ["pop"]

    def test_unknown_mood_returns_empty(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": [], "disliked": []},
                    "mood_bias": {},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        assert manager.get_mood_recommendations("nonexistent") == []


class TestArtistConstraints:
    def test_returns_liked_and_disliked(self, tmp_path) -> None:
        profile_path = tmp_path / "user_profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "core_taste": [],
                    "artist_preference": {"liked": ["A", "B"], "disliked": ["C"]},
                    "mood_bias": {},
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
        constraints = manager.get_artist_constraints()
        assert constraints["liked"] == ["A", "B"]
        assert constraints["disliked"] == ["C"]
