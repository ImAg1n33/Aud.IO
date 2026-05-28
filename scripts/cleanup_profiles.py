"""One-shot cleanup: strip invalid mood keys from all user_profile files (RFC-005).

Usage:
    python scripts/cleanup_profiles.py
"""

import sys
from pathlib import Path

# Ensure backend is on sys.path (run from repo root)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.memory.profile_schema import VALID_MOODS, atomic_write_json, load_profile  # noqa: E402


def main() -> None:
    memory_dir = Path(__file__).resolve().parents[1] / "backend" / "memory"
    profiles = list(memory_dir.glob("user_profile*.json"))
    cleaned_count = 0
    removed_total = 0

    for path in profiles:
        profile = load_profile(path).to_dict()
        mood_bias = profile.get("mood_bias", {})
        if not isinstance(mood_bias, dict):
            continue

        cleaned = {k: v for k, v in mood_bias.items() if k in VALID_MOODS}
        removed = len(mood_bias) - len(cleaned)

        if removed > 0:
            profile["mood_bias"] = cleaned
            atomic_write_json(path, profile)
            removed_keys = set(mood_bias.keys()) - set(cleaned.keys())
            print(f"[CLEANED] {path.name}: removed {removed} invalid mood key(s): {removed_keys}")
            cleaned_count += 1
            removed_total += removed
        else:
            print(f"[OK]      {path.name}: no invalid mood keys")

    print(f"\nDone. {cleaned_count} file(s) cleaned, {removed_total} key(s) removed.")


if __name__ == "__main__":
    main()
