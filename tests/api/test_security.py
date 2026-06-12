import uuid

import pytest

from backend.api._security import normalize_session_id


class TestNormalizeSessionId:
    def test_accepts_standard_uuid(self) -> None:
        uid = str(uuid.uuid4())
        assert normalize_session_id(uid) == uid

    def test_accepts_uuid_hex(self) -> None:
        uid = uuid.uuid4().hex
        assert normalize_session_id(uid) == uid

    def test_accepts_safe_alphanumeric_slug(self) -> None:
        assert normalize_session_id("test-session_42") == "test-session_42"

    def test_generates_fresh_uuid_when_none(self) -> None:
        result = normalize_session_id(None)
        uuid.UUID(result)  # does not raise

    def test_generates_fresh_uuid_when_empty(self) -> None:
        result = normalize_session_id("")
        uuid.UUID(result)

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            normalize_session_id("../../etc/passwd")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            normalize_session_id("evil\\windows\\path")

    def test_rejects_control_characters(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            normalize_session_id("hello\nworld")
