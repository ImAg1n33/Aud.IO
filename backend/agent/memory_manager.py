import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from backend.agent.prompt_builder import build_memory_observer_messages


logger = logging.getLogger(__name__)


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
        return profile

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
        config = self._memory_model_config()
        if not config["api_key"]:
            self._append_audit_log({"status": "skipped", "reason": "MEMORY_API_KEY/LLM_API_KEY missing"})
            return []

        endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
        errors: list[str] = []
        for model in self._candidate_models(config["model"]):
            body = {
                "model": model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": build_memory_observer_messages(old_profile, user_input, assistant_reply),
            }

            req = Request(
                endpoint,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config['api_key']}",
                },
                method="POST",
            )

            try:
                with urlopen(req, timeout=60) as resp:
                    raw = resp.read().decode("utf-8")
                completion = json.loads(raw)
                content = completion["choices"][0]["message"]["content"]
                parsed = self._extract_json_object(content)

                # No obvious preference changes.
                if parsed == {}:
                    self._append_audit_log({"status": "no_change", "model": model})
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
                        "model": model,
                        "patch_count": len(normalized),
                    }
                )
                return normalized
            except HTTPError as exc:
                detail = self._read_http_error_detail(exc)
                errors.append(f"{model}: HTTP {exc.code} {detail}")
                logger.warning("Memory model request failed with HTTP %s: %s", exc.code, detail)
            except (URLError, TimeoutError, KeyError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{model}: {exc}")
                logger.warning("Memory model request failed: %s", exc)

        if errors:
            self._append_audit_log({"status": "failed", "errors": errors})
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

    def _memory_model_config(self) -> dict[str, str]:
        base_url = os.getenv("MEMORY_BASE_URL", "").strip() or os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
        model = os.getenv("MEMORY_MODEL", "").strip() or os.getenv("LLM_MODEL", "deepseek-chat").strip()
        api_key = os.getenv("MEMORY_API_KEY", "").strip() or os.getenv("LLM_API_KEY", "").strip()
        return {
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
        }

    def _candidate_models(self, configured_model: str) -> list[str]:
        candidates = [
            configured_model.strip(),
            os.getenv("LLM_MODEL", "").strip(),
            "deepseek-chat",
        ]

        deduped: list[str] = []
        seen: set[str] = set()
        for model in candidates:
            if not model or model in seen:
                continue
            seen.add(model)
            deduped.append(model)
        return deduped

    def _read_http_error_detail(self, exc: HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8")
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                error = parsed.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str):
                        return message
            return body
        except Exception:
            return str(exc)

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(stripped[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("Model output is not valid JSON object.")

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
