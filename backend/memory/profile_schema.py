"""Pydantic models and atomic write for user_profile.json — the last line of defence
against LLM-generated JSON Patch corruption."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ============================================================
# Models
# ============================================================

class ArtistPreference(BaseModel):
    liked: list[str] = []
    disliked: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _coerce_lists(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for field_name in ("liked", "disliked"):
            if field_name in data:
                data[field_name] = _coerce_string_list(data[field_name])
        return data


class UserProfile(BaseModel):
    core_taste: list[str] = []
    artist_preference: ArtistPreference = Field(default_factory=ArtistPreference)
    mood_bias: dict[str, list[str]] = {}
    last_updated: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_core_taste(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "core_taste" in data:
            data["core_taste"] = _coerce_string_list(data["core_taste"])
        if "mood_bias" in data and isinstance(data["mood_bias"], dict):
            cleaned: dict[str, list[str]] = {}
            for key, value in data["mood_bias"].items():
                key_str = str(key).strip()
                if key_str:
                    cleaned[key_str] = _coerce_string_list(value)
            data["mood_bias"] = cleaned
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return list(dict.fromkeys(items))  # dedupe preserve order
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ============================================================
# Atomic write
# ============================================================

def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ============================================================
# Load / validate helper
# ============================================================

def load_profile(path: Path) -> UserProfile:
    """Read and validate user_profile.json. Returns default if file missing or corrupt."""
    default = UserProfile(last_updated=_utc_now_iso())

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, default.to_dict())
        return default

    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return default

    if not isinstance(parsed, dict):
        return default

    try:
        profile = UserProfile.model_validate(parsed)
        if not profile.last_updated or not profile.last_updated.strip():
            profile.last_updated = _utc_now_iso()
        return profile
    except Exception:
        # If the LLM somehow wrote an unrecoverable schema, fall back to default
        return default
