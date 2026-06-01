"""Short-term, in-memory conversation history."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ConversationTurn:
    user_input: str
    assistant_reply: str = ""
    timestamp: str = ""
    intent: str = ""
    played_song: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )


class ConversationMemory:
    def __init__(self, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self._turns: list[ConversationTurn] = []

    def add_turn(
        self,
        user_input: str,
        assistant_reply: str = "",
        intent: str = "",
        played_song: dict[str, Any] | None = None,
    ) -> None:
        turn = ConversationTurn(
            user_input=user_input,
            assistant_reply=assistant_reply,
            intent=intent,
            played_song=played_song,
        )
        self._turns.append(turn)
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns:]

    def get_history(self, last_n: int | None = None) -> list[ConversationTurn]:
        if last_n is None:
            return list(self._turns)
        return self._turns[-last_n:]

    def get_last_user_message(self) -> str | None:
        for turn in reversed(self._turns):
            if turn.user_input.strip():
                return turn.user_input
        return None

    def get_last_assistant_reply(self) -> str | None:
        for turn in reversed(self._turns):
            if turn.assistant_reply.strip():
                return turn.assistant_reply
        return None

    def format_history(self, last_n: int | None = None) -> str:
        """Format recent turns as a readable block for LLM prompt injection."""
        turns = self.get_history(last_n)
        if not turns:
            return ""
        lines = []
        for i, turn in enumerate(turns, 1):
            user = turn.user_input.strip()
            reply = turn.assistant_reply.strip()
            if user:
                lines.append(f"User: {user}")
            if reply:
                lines.append(f"Aud.IO: {reply}")
            if turn.played_song:
                name = turn.played_song.get("name", "")
                artist = turn.played_song.get("artist", "")
                if name:
                    lines.append(f"[Played: {artist} - {name}]")
        return "\n".join(lines)

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)
