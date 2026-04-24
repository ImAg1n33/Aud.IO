import json

import pytest

from backend.agent.memory_manager import MemoryManager


class FakePatchMemoryManager(MemoryManager):
    def _request_patch_from_model(self, user_input, assistant_reply, old_profile):
        return [
            {"op": "add", "path": "/core_taste/-", "value": "jazz"},
            {"op": "add", "path": "/artist_preference/liked/-", "value": "Miles Davis"},
        ]


class FakeNoChangeMemoryManager(MemoryManager):
    def _request_patch_from_model(self, user_input, assistant_reply, old_profile):
        return []


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
