import json
from typing import Any

from backend.agent.llm_client import call_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompt_builder import build_prompt
from backend.tools.netease_api import get_song_mp3_url, search_first_song


class AssistantService:
    def __init__(self, memory_manager: MemoryManager | None = None) -> None:
        self.memory_manager = memory_manager or MemoryManager()

    def generate_reply(self, user_input: str, context: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
        merged_context: dict[str, Any] = dict(context or {})

        try:
            merged_context["user_profile"] = self.memory_manager.get_profile()
        except Exception as exc:
            merged_context["user_profile_error"] = str(exc)

        prompt = build_prompt(user_input, merged_context)
        reply = call_llm(prompt)
        final_reply = self._attach_music_result(reply)
        return final_reply, prompt

    def schedule_profile_update(
        self,
        background_tasks: Any,
        user_input: str,
        final_reply: dict[str, Any],
    ) -> None:
        background_tasks.add_task(
            self.memory_manager.async_update_profile,
            user_input,
            json.dumps(final_reply, ensure_ascii=False),
        )

    def _extract_play_keyword(self, reply: dict[str, Any]) -> str:
        direct = reply.get("play_keyword")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()

        actions = reply.get("actions", [])
        if not isinstance(actions, list):
            return ""

        for item in actions:
            if isinstance(item, dict):
                action_type = str(item.get("type", "")).strip().lower()
                if action_type in {"play_music", "play_song", "music_search"}:
                    keyword = item.get("keyword") or item.get("play_keyword")
                    if isinstance(keyword, str) and keyword.strip():
                        return keyword.strip()
                continue

            if not isinstance(item, str):
                continue

            text = item.strip()
            if not text:
                continue

            for prefix in ["play_music:", "play_song:", "music_search:", "play:", "播放:"]:
                if text.lower().startswith(prefix.lower()):
                    keyword = text[len(prefix) :].strip()
                    if keyword:
                        return keyword

        return ""

    def _attach_music_result(self, reply: dict[str, Any]) -> dict[str, Any]:
        keyword = self._extract_play_keyword(reply)
        if not keyword:
            return reply

        enriched = dict(reply)
        try:
            song = search_first_song(keyword)
            mp3_url = get_song_mp3_url(song["id"])
            enriched["music"] = {
                "requested_keyword": keyword,
                "song_id": song["id"],
                "name": song["name"],
                "artist": song["artist"],
                "mp3_url": mp3_url,
            }
        except Exception as exc:
            enriched["music"] = {
                "requested_keyword": keyword,
                "error": str(exc),
            }
        return enriched
