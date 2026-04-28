import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os

from dotenv import load_dotenv

from backend.agent.llm_client import request_json_object
from backend.agent.prompt_builder import build_memory_observer_messages


logger = logging.getLogger(__name__)
SLOW_CRITIC_MODEL = os.getenv("MEMORY_MODEL", "deepseek-reasoner")


class MemoryManager:
    def __init__(
        self,
        profile_path: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        self.profile_path = profile_path or (backend_root / "memory" / "user_profile.json")
        self.env_path = env_path or (backend_root / ".env")
        self.audit_log_path = backend_root / "memory" / "memory_update.log"
        load_dotenv(self.env_path)

    def get_profile(self) -> dict[str, Any]:
        """Synchronously read user_profile.json from disk."""
        if not self.profile_path.exists():
            default_profile = self._default_profile()
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            self.profile_path.write_text(
                json.dumps(default_profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return default_profile

        raw = self.profile_path.read_text(encoding="utf-8")
        profile = json.loads(raw)
        if not isinstance(profile, dict):
            raise ValueError("user_profile.json must be a JSON object.")
        normalized = self._normalize_profile_schema(profile)
        return normalized

    async def async_update_profile(self, user_input: str, assistant_reply: str) -> dict[str, Any]:
        """Asynchronously update profile using a small model and JSON Patch.

        The whole flow is non-blocking for event loop:
        - file I/O via asyncio.to_thread
        - model HTTP request via asyncio.to_thread
        """
        current_profile = await asyncio.to_thread(self.get_profile)
        patch_ops = await asyncio.to_thread(
            self._request_patch_from_model,
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

        updated_profile = await asyncio.to_thread(self._apply_patch_safe, current_profile, patch_ops)
        updated_profile = self._normalize_profile_schema(updated_profile)
        updated_profile["last_updated"] = self._utc_now_iso()

        await asyncio.to_thread(
            self.profile_path.write_text,
            json.dumps(updated_profile, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )

        await asyncio.to_thread(
            self._append_audit_log,
            {
                "status": "updated",
                "patch_count": len(patch_ops),
                "last_updated": updated_profile["last_updated"],
            },
        )
        return updated_profile

    def _request_patch_from_model(
        self,
        user_input: str,
        assistant_reply: str,
        old_profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        messages = build_memory_observer_messages(old_profile, user_input, assistant_reply)

        try:
            parsed = request_json_object(messages=messages, model=SLOW_CRITIC_MODEL, temperature=0.1)

            # No obvious preference changes.
            if parsed == {}:
                self._append_audit_log({"status": "no_change", "model": SLOW_CRITIC_MODEL})
                return []

            if isinstance(parsed.get("patch"), list):
                normalized = self._normalize_patch(parsed["patch"])
            else:
                # Compatibility: if model returns direct update object, convert to replace ops.
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
        return path == "/core_taste" or path.startswith("/core_taste/") or path == "/artist_preference" or path.startswith(
            "/artist_preference/"
        ) or path == "/mood_bias" or path.startswith("/mood_bias/")

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

    def _default_profile(self) -> dict[str, Any]:
        return {
            "core_taste": [],
            "artist_preference": {"liked": [], "disliked": []},
            "mood_bias": {},
            "last_updated": self._utc_now_iso(),
        }

    def _normalize_profile_schema(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(profile)

        core_taste = normalized.get("core_taste", [])
        if isinstance(core_taste, list):
            normalized["core_taste"] = [str(item).strip() for item in core_taste if str(item).strip()]
        elif isinstance(core_taste, str) and core_taste.strip():
            normalized["core_taste"] = [core_taste.strip()]
        else:
            normalized["core_taste"] = []

        artist_preference = normalized.get("artist_preference", {})
        if not isinstance(artist_preference, dict):
            artist_preference = {}

        liked = artist_preference.get("liked", [])
        disliked = artist_preference.get("disliked", [])

        artist_preference["liked"] = self._coerce_string_list(liked)
        artist_preference["disliked"] = self._coerce_string_list(disliked)
        normalized["artist_preference"] = artist_preference

        mood_bias = normalized.get("mood_bias", {})
        if not isinstance(mood_bias, dict):
            mood_bias = {}
        cleaned_mood_bias: dict[str, list[str]] = {}
        for key, value in mood_bias.items():
            key_str = str(key).strip()
            if not key_str:
                continue
            cleaned_mood_bias[key_str] = self._coerce_string_list(value)
        normalized["mood_bias"] = cleaned_mood_bias

        last_updated = normalized.get("last_updated")
        if not isinstance(last_updated, str) or not last_updated.strip():
            normalized["last_updated"] = self._utc_now_iso()

        return normalized

    def _coerce_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return list(dict.fromkeys(items))
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _append_audit_log(self, payload: dict[str, Any]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": self._utc_now_iso(),
            **payload,
        }
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
