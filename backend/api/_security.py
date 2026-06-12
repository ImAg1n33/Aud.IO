import re
import uuid

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def normalize_session_id(raw: str | None) -> str:
    """Validate and normalize a client-supplied session_id.

    Accepts:
    - UUID strings (any standard format)
    - Alphanumeric + dash/underscore, 1-64 chars
    - None → generates a fresh UUID

    Raises ValueError for inputs containing path separators,
    control characters, or other unsafe patterns.
    """
    if raw is None:
        return uuid.uuid4().hex

    raw = raw.strip()
    if not raw:
        return uuid.uuid4().hex

    try:
        uuid.UUID(raw)
        return raw
    except (ValueError, AttributeError):
        pass

    if _SESSION_ID_RE.match(raw):
        return raw

    raise ValueError(f"Invalid session_id: {raw!r}")
