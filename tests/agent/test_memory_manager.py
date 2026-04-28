import json
from unittest.mock import Mock

import pytest

import backend.agent.memory_manager as memory_manager_module
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

    mocked_request = Mock(
        return_value={
            "patch": [
                {"op": "add", "path": "/core_taste/-", "value": "jazz"},
            ]
        }
    )
    monkeypatch.setattr(memory_manager_module, "request_json_object", mocked_request)

    manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("我最近更喜欢爵士", "收到，我会按这个方向推荐")

    mocked_request.assert_called_once()
    _, kwargs = mocked_request.call_args
    assert kwargs["model"] == "deepseek-reasoner"
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

    mocked_request = Mock(return_value={})
    monkeypatch.setattr(memory_manager_module, "request_json_object", mocked_request)

    manager = MemoryManager(profile_path=profile_path, env_path=tmp_path / ".env")
    updated = await manager.async_update_profile("这次没有新增偏好", "明白，不更新记忆")

    mocked_request.assert_called_once()

    persisted_text = profile_path.read_text(encoding="utf-8")
    assert persisted_text == original_text
    assert persisted_text.strip() != ""
    assert json.loads(persisted_text) == original_profile
    assert updated == original_profile
