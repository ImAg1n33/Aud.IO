import json
from typing import Any

from backend.agent.llm_client import call_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompt_builder import build_prompt
from backend.tools.netease_api import get_song_mp3_url, search_first_song


class AssistantService:
    MAX_RETRIES = 2

    def __init__(self, memory_manager: MemoryManager | None = None) -> None:
        self.memory_manager = memory_manager or MemoryManager()

    def generate_reply(self, user_input: str, context: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
        merged_context: dict[str, Any] = dict(context or {})

        try:
            merged_context["user_profile"] = self.memory_manager.get_profile()
        except Exception as exc:
            merged_context["user_profile_error"] = str(exc)

        working_input = user_input
        retry_count = 0

        while True:
            prompt = build_prompt(working_input, merged_context)
            reply = call_llm(prompt, model="deepseek-chat")
            final_reply = self._attach_music_result(reply)

            tool_error = self._extract_unplayable_music_error(final_reply)
            if not tool_error:
                return final_reply, prompt

            if retry_count >= self.MAX_RETRIES:
                degraded = self._build_graceful_music_fallback(final_reply)
                return degraded, prompt

            retry_count += 1
            feedback = self._build_music_recovery_feedback(final_reply)
            working_input = f"{user_input}\n\n{feedback}"

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

    def _extract_unplayable_music_error(self, reply: dict[str, Any]) -> str | None:
        music = reply.get("music")
        if not isinstance(music, dict):
            return None

        error = music.get("error")
        if not isinstance(error, str) or not error.strip():
            return None

        lowered = error.lower()
        markers = [
            "版权",
            "copyright",
            "no playable url",
            "failed to fetch song url",
            "无法播放",
        ]
        if any(marker in lowered for marker in markers):
            return error.strip()
        return None

    def _build_music_recovery_feedback(self, reply: dict[str, Any]) -> str:
        music = reply.get("music") if isinstance(reply.get("music"), dict) else {}
        song_name = music.get("name") if isinstance(music, dict) else None
        keyword = music.get("requested_keyword") if isinstance(music, dict) else None

        target = "上一首歌"
        if isinstance(song_name, str) and song_name.strip():
            target = song_name.strip()
        elif isinstance(keyword, str) and keyword.strip():
            target = keyword.strip()

        return (
            f"系统提示：刚才你推荐的歌曲 {target} 因为版权限制无法播放。"
            "请立刻重新推荐一首与之前完全不同、且大概率有免费版权的歌曲，并填入 play_keyword！"
        )

    def _build_graceful_music_fallback(self, reply: dict[str, Any]) -> dict[str, Any]:
        degraded = dict(reply)
        degraded.pop("music", None)
        degraded["play_keyword"] = ""

        fallback_text = "抱歉，我为您连续挑选了几首歌，都因为版权限制无法播放，请尝试更换一种风格或指定其他歌手。"
        degraded["answer"] = fallback_text
        degraded["say"] = fallback_text

        actions = degraded.get("actions")
        if isinstance(actions, list):
            filtered: list[Any] = []
            for item in actions:
                if isinstance(item, str) and "play" in item.lower():
                    continue
                if isinstance(item, dict):
                    action_type = str(item.get("type", "")).strip().lower()
                    if any(token in action_type for token in ["play", "music"]):
                        continue
                filtered.append(item)
            degraded["actions"] = filtered

        return degraded
