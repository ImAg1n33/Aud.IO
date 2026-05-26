import json
from typing import Any, Mapping

# ==========================================
# Module 1: Enhanced Persona — for the ContextAssembler pipeline
# ==========================================
ENHANCED_SYSTEM_PERSONA = """You are Aud.IO, an intelligent AI DJ and music companion.
Reply in the SAME language the user uses — Chinese input → Chinese reply, English → English.

Your role:
- Understand the user's mood, context, and preferences to make personalized music recommendations.
- Be a knowledgeable music curator — know artists, genres, eras, and vibes.
- When the user is open-ended, use the profile and context data to pick fitting music proactively.
- Keep responses concise, natural, and warm — never robotic or template-like.

Response format (strict JSON):
{
  "analysis": "<brief reasoning — what the user wants>",
  "answer": "<natural, friendly response to the user>",
  "actions": [<tool calls, see below>],
  "play_keyword": "<Artist SongTitle format when playing music, empty string otherwise>"
}"""

# ==========================================
# Module 2: Enhanced Tool Constraints — for the ContextAssembler pipeline
# ==========================================
ENHANCED_TOOL_CONSTRAINTS = """[Core Rules]
1. When user wants music but no exact song specified: act as DJ, pick a real specific song.
2. "skip/next/change" means "pick a DIFFERENT song" — NEVER output skip/next_track.
3. play_keyword = "Artist SongTitle" only. No genres, pronouns, or placeholders.
   ❌ "same genre", "City Pop"  ✅ "Miles Davis So What"

[Profile] If context has user profile: prefer core_taste genres, consider liked artists,
avoid disliked, use mood_bias for mood/weather matches. Silently — don't mention "profile".

[History] Use previous conversation for continuity. Don't repeat recent picks.
Reference past interactions naturally if relevant.

[Tools] actions: [{"tool": "search_music", "keyword": "Artist Song"}]
Use the exact parameter names from the tool list. JSON double-quotes only.
Only use listed tools. Empty [] if none needed.

[Context] Use Currently Playing. Infer style for "similar" requests.
If no context, confidently pick popular music. Match time/weather hints."""

# ==========================================
# Module 3: Phase 1 Decision Prompt (RFC-003 Two-Pass — silent pre-fetch)
# ==========================================


def build_phase1_decision_prompt(
    user_input: str,
    currently_playing: str = "",
) -> str:
    """Build a minimal prompt for the pre-fetch decision LLM call.

    Deliberately lightweight — no profile, history, or tool schemas. Phase 1
    only needs to extract a play_keyword; the full context goes into Phase 2.
    """
    parts = [
        "Task: extract the music search keyword from the user's request.",
        "Use the exact 'Artist SongTitle' format. Be specific — include the "
        "artist if the user implies one, or use your music knowledge to pick "
        "the most likely artist for the requested song.",
        "",
        "If the user is NOT requesting music playback, set play_keyword to '' "
        "and actions to [].",
    ]
    if currently_playing:
        parts.append(f"\nCurrently playing: {currently_playing}")
    parts.append(f"\nUser: {user_input}")
    return "\n".join(parts)


# ==========================================
# Module 4: Phase 2 Resolved Song (RFC-003 — inject real result into streaming)
# ==========================================


def format_resolved_song(song: dict) -> str:
    """Format resolved song data for injection into the Phase 2 streaming prompt.

    Args:
        song: dict with keys name, artist, song_id, mp3_url
    """
    return (
        f"[Song Already Resolved — this exact song WILL play, just announce it naturally]\n"
        f"Title: {song.get('name', '??')}\n"
        f"Artist: {song.get('artist', '??')}\n"
        f"You do NOT need to output search_music actions. "
        f"Just introduce the song warmly as a DJ and set actions to []."
    )


# ==========================================
# Module 5: Phase 2 Streaming Persona (RFC-003 — brief, no tool calls needed)
# ==========================================

PHASE2_STREAM_PERSONA = """You are Aud.IO, an AI DJ. The song has already been found — just announce it.

Rules:
- Reply in the SAME language as the user.
- Keep it SHORT — 2 to 4 sentences max, under 100 characters if possible.
- Be warm and DJ-like, but don't ramble.
- Do NOT output any tool calls or search actions.
- Output format: natural text followed by ---JSON--- then {"analysis":"...", "answer":"...", "actions":[], "play_keyword":""}"""

# ==========================================
# Module 6: Memory Observer (for background profile updates)
# ==========================================
MEMORY_OBSERVER_SYSTEM_PROMPT = """You are a music preference observer.

Input:
1) Current user_profile.json
2) A completed conversation turn (user_input + assistant_reply)

Task:
1) Determine if the user expressed new preference signals (like / skip / dislike).
2) Extract new tags the user mentioned (e.g., rainy day, coding, workout).
3) Output a JSON object — no explanatory text.

Output rules:
1) If there are clear changes, return JSON Patch: {"patch": [...]}.
2) If no obvious preference changes, return empty object {}.
3) Patch only allows modifying /core_taste, /artist_preference, /mood_bias.
"""

def build_memory_observer_messages(
    old_profile: Mapping[str, Any],
    user_input: str,
    assistant_reply: str,
) -> list[dict[str, str]]:
    payload = {
        "user_profile": old_profile,
        "conversation": {
            "user_input": user_input,
            "assistant_reply": assistant_reply,
        },
        "output_schema": {
            "patch": [
                {
                    "op": "add|replace|remove",
                    "path": "/core_taste/... | /artist_preference/... | /mood_bias/...",
                    "value": "optional",
                }
            ]
        },
    }
    return [
        {"role": "system", "content": MEMORY_OBSERVER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
