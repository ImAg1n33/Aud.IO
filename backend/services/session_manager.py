"""Per-session context with TTL-based automatic expiry.

SessionContext bundles all mutable per-user state so that concurrent users
never pollute each other's conversation history, episodic recall, or preferences.
Sessions idle beyond TTL are evicted automatically by cachetools.TTLCache.
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from cachetools import TTLCache

from backend.memory.conversation_memory import ConversationMemory

if TYPE_CHECKING:
    from backend.agent.context_assembler import ContextAssembler
    from backend.agent.memory_manager import MemoryManager


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionContext:
    """All per-session mutable state."""

    __slots__ = (
        "session_id",
        "created_at",
        "short_term_memory",
        "memory_manager",
        "context_assembler",
        "last_summary_turn",
    )

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.created_at = _utc_now()
        self.short_term_memory: ConversationMemory = ConversationMemory(max_turns=20)
        self.memory_manager: "MemoryManager | None" = None
        self.context_assembler: "ContextAssembler | None" = None
        # Reflection 节流：上次摘要时的轮数（0 = 尚未摘要）
        self.last_summary_turn: int = 0


class SessionManager:
    """TTL-based session pool.

    Sessions are evicted after *ttl* seconds of inactivity (last access).
    *maxsize* caps total concurrent sessions to bound memory.
    """

    def __init__(self, ttl: int = 86400, maxsize: int = 100) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)

    # ---- TTL cache delegates ----

    def get_or_create(self, session_id: str | None) -> SessionContext:
        """Return existing session or create a new one.

        If *session_id* is None or unknown a fresh UUID-based id is generated.
        """
        sid = session_id.strip() if session_id else ""
        if sid and sid in self._cache:
            return self._cache[sid]

        new_id = sid or str(uuid.uuid4())
        ctx = SessionContext(new_id)
        self._cache[new_id] = ctx
        return ctx

    def get(self, session_id: str) -> SessionContext | None:
        return self._cache.get(session_id)

    def remove(self, session_id: str) -> None:
        self._cache.pop(session_id, None)

    def heartbeat(self, session_id: str) -> bool:
        """Touch a session to reset its TTL. Returns False if session is gone."""
        ctx = self._cache.get(session_id)
        if ctx is None:
            return False
        # Re-insert to refresh the TTL (TTLCache tracks access on __getitem__,
        # but an explicit touch makes intent clearer and works even if the
        # underlying cache implementation changes.)
        self._cache[session_id] = ctx
        return True

    # ---- introspection ----

    @property
    def active_count(self) -> int:
        return len(self._cache)

    def list_active(self) -> list[str]:
        return list(self._cache.keys())
