import asyncio
import json
import logging
from pathlib import Path
from typing import Any
import os

from backend.agent.llm_client import request_json_object
from backend.agent.prompt_builder import build_memory_observer_messages
from backend.data_config import get_data_dir, get_profiles_dir
from backend.memory.profile_schema import (
    UserProfile,
    atomic_write_json,
    load_profile,
    _utc_now_iso,
    _coerce_string_list,
)


logger = logging.getLogger(__name__)
SLOW_CRITIC_MODEL = os.getenv("MEMORY_MODEL", "").strip()


class MemoryManager:
    def __init__(
        self,
        profile_path: Path | None = None,
        env_path: Path | None = None,
        session_id: str = "default",
    ) -> None:
        if profile_path:
            self.profile_path = profile_path
        else:
            profiles_dir = get_profiles_dir()
            profiles_dir.mkdir(parents=True, exist_ok=True)
            if session_id and session_id != "default":
                self.profile_path = profiles_dir / f"user_profile_{session_id}.json"
            else:
                self.profile_path = profiles_dir / "user_profile.json"
        self.audit_log_path = get_data_dir() / "memory_update.log"
        self.session_id = session_id

    def get_profile(self) -> dict[str, Any]:
        """Synchronously read and validate user_profile.json. Returns dict for backward compat."""
        profile = load_profile(self.profile_path)
        return profile.to_dict()

    async def async_update_profile(self, user_input: str, assistant_reply: str) -> dict[str, Any]:
        """Asynchronously update profile using a small model and JSON Patch.

        Flow: read → model proposes patch → apply → validate with Pydantic → atomic write.
        """
        current_profile = await asyncio.to_thread(self.get_profile)
        patch_ops = await self._request_patch_from_model(
            user_input,
            assistant_reply,
            current_profile,
        )

        if not patch_ops:
            await asyncio.to_thread(
                self._append_audit_log,
                {
                    "status": "no_patch",
                    "reason": "model returned empty patch or no preference changes",
                },
            )
            return current_profile

        # Apply JSON Patch on raw dict
        updated_dict = await asyncio.to_thread(self._apply_patch_safe, current_profile, patch_ops)

        # Validate & normalize through Pydantic (last line of defence against LLM output)
        try:
            validated = UserProfile.model_validate(updated_dict)
        except Exception as exc:
            logger.warning("Pydantic validation failed after patch — discarding update: %s", exc)
            await asyncio.to_thread(
                self._append_audit_log,
                {"status": "validation_failed", "error": str(exc)},
            )
            return current_profile

        validated.last_updated = _utc_now_iso()
        validated_dict = validated.to_dict()

        # Atomic write — won't corrupt on crash
        await asyncio.to_thread(atomic_write_json, self.profile_path, validated_dict)

        await asyncio.to_thread(
            self._append_audit_log,
            {
                "status": "updated",
                "patch_count": len(patch_ops),
                "last_updated": validated.last_updated,
            },
        )
        return validated_dict

    def get_preference_summary(self) -> str:
        """Produce an LLM-readable summary of the user profile for prompt injection."""
        profile = self.get_profile()
        parts: list[str] = []

        core = profile.get("core_taste", [])
        if core:
            parts.append(f"Preferred genres: {', '.join(core)}.")

        liked = profile.get("artist_preference", {}).get("liked", [])
        if liked:
            parts.append(f"Liked artists: {', '.join(liked)}.")

        disliked = profile.get("artist_preference", {}).get("disliked", [])
        if disliked:
            parts.append(f"Avoid these artists: {', '.join(disliked)}.")

        mood_bias = profile.get("mood_bias", {})
        if mood_bias:
            mood_lines = []
            for mood, genres in mood_bias.items():
                if genres:
                    mood_lines.append(f"  {mood} → {', '.join(genres)}")
            if mood_lines:
                parts.append("Mood-to-genre mapping:\n" + "\n".join(mood_lines))

        return "\n".join(parts) if parts else "No music preferences recorded yet."

    def get_mood_recommendations(self, mood: str) -> list[str]:
        """Get genre/artist recommendations from mood_bias for a given mood tag."""
        if not mood:
            return []
        profile = self.get_profile()
        mood_bias = profile.get("mood_bias", {})
        if not isinstance(mood_bias, dict):
            return []

        mood_lower = mood.strip().lower()
        for key, genres in mood_bias.items():
            if key.lower() == mood_lower:
                if isinstance(genres, list):
                    return [str(g).strip() for g in genres if str(g).strip()]
        return []

    def get_artist_constraints(self) -> dict[str, list[str]]:
        """Extract liked/disliked artist lists for prompt constraints."""
        profile = self.get_profile()
        prefs = profile.get("artist_preference", {})
        if not isinstance(prefs, dict):
            return {"liked": [], "disliked": []}
        return {
            "liked": _coerce_string_list(prefs.get("liked", [])),
            "disliked": _coerce_string_list(prefs.get("disliked", [])),
        }

    # ================================================================
    # Private: JSON Patch engine (unchanged)
    # ================================================================

    async def _request_patch_from_model(
        self,
        user_input: str,
        assistant_reply: str,
        old_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        messages = build_memory_observer_messages(old_profile, user_input, assistant_reply)

        try:
            parsed = await request_json_object(messages=messages, model=SLOW_CRITIC_MODEL, temperature=0.1)

            if parsed == {}:
                self._append_audit_log({"status": "no_change", "model": SLOW_CRITIC_MODEL})
                return []

            if isinstance(parsed.get("patch"), list):
                normalized = self._normalize_patch(parsed["patch"])
            else:
                object_patch = self._convert_object_to_patch(parsed)
                normalized = self._normalize_patch(object_patch)

            self._append_audit_log(
                {
                    "status": "model_ok",
                    "model": SLOW_CRITIC_MODEL,
                    "patch_count": len(normalized),
                }
            )
            return normalized
        except Exception as exc:
            logger.warning("Memory model request failed: %s", exc)
            self._append_audit_log({"status": "failed", "model": SLOW_CRITIC_MODEL, "error": str(exc)})
            return []

    def _normalize_patch(self, patch: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in patch:
            if not isinstance(item, dict):
                continue
            op = item.get("op")
            path = item.get("path")
            if not isinstance(op, str) or not isinstance(path, str):
                continue
            if not self._is_allowed_patch_path(path):
                continue
            safe_item: dict[str, Any] = {"op": op.strip().lower(), "path": path}
            if "value" in item:
                safe_item["value"] = item["value"]
            normalized.append(safe_item)
        return normalized

    def _convert_object_to_patch(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        patch: list[dict[str, Any]] = []
        for field in ("core_taste", "artist_preference", "mood_bias"):
            if field in payload:
                patch.append({"op": "replace", "path": f"/{field}", "value": payload[field]})
        return patch

    def _is_allowed_patch_path(self, path: str) -> bool:
        return (
            path == "/core_taste" or path.startswith("/core_taste/")
            or path == "/artist_preference" or path.startswith("/artist_preference/")
            or path == "/mood_bias" or path.startswith("/mood_bias/")
        )

    def _apply_patch_safe(self, source: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
        doc = json.loads(json.dumps(source))
        for op in ops:
            self._apply_single_op(doc, op)
        return doc

    def _apply_single_op(self, doc: dict[str, Any], op: dict[str, Any]) -> None:
        kind = str(op.get("op", "")).lower()
        path = op.get("path")
        if not isinstance(path, str):
            return
        tokens = self._parse_pointer(path)
        if not tokens:
            if kind in {"add", "replace"} and isinstance(op.get("value"), dict):
                doc.clear()
                doc.update(op["value"])
            return
        parent, last = self._resolve_parent(doc, tokens)
        if kind == "add":
            self._add_value(parent, last, op.get("value"))
        elif kind == "replace":
            self._replace_value(parent, last, op.get("value"))
        elif kind == "remove":
            self._remove_value(parent, last)

    def _parse_pointer(self, path: str) -> list[str]:
        if path == "":
            return []
        if not path.startswith("/"):
            raise ValueError("Invalid JSON Pointer path.")
        tokens = path[1:].split("/")
        return [token.replace("~1", "/").replace("~0", "~") for token in tokens if token != ""]

    def _resolve_parent(self, root: Any, tokens: list[str]) -> tuple[Any, str]:
        current = root
        for token in tokens[:-1]:
            if isinstance(current, dict):
                if token not in current or not isinstance(current[token], (dict, list)):
                    current[token] = {}
                current = current[token]
            elif isinstance(current, list):
                idx = self._to_list_index(token, len(current), allow_end=False)
                current = current[idx]
            else:
                raise ValueError("Invalid patch parent container.")
        return current, tokens[-1]

    def _add_value(self, parent: Any, token: str, value: Any) -> None:
        if isinstance(parent, dict):
            parent[token] = value
            return
        if isinstance(parent, list):
            if token == "-":
                parent.append(value)
                return
            idx = self._to_list_index(token, len(parent), allow_end=True)
            parent.insert(idx, value)

    def _replace_value(self, parent: Any, token: str, value: Any) -> None:
        if isinstance(parent, dict):
            parent[token] = value
            return
        if isinstance(parent, list):
            idx = self._to_list_index(token, len(parent), allow_end=False)
            parent[idx] = value

    def _remove_value(self, parent: Any, token: str) -> None:
        if isinstance(parent, dict):
            parent.pop(token, None)
            return
        if isinstance(parent, list):
            idx = self._to_list_index(token, len(parent), allow_end=False)
            parent.pop(idx)

    def _to_list_index(self, token: str, size: int, allow_end: bool) -> int:
        if token == "-" and allow_end:
            return size
        idx = int(token)
        if idx < 0:
            raise ValueError("Negative list index is not allowed.")
        if allow_end:
            if idx > size:
                raise ValueError("List index out of range.")
        elif idx >= size:
            raise ValueError("List index out of range.")
        return idx

    def _append_audit_log(self, payload: dict[str, Any]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": _utc_now_iso(),
            **payload,
        }
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
