"""Backward-compatibility re-exports — all prompt definitions now live in prompts.py.

RFC-007 Plan B: This file exists only so existing imports don't break.
New code should import directly from backend.agent.prompts.
"""

# Re-export everything from the centralized registry
from backend.agent.prompts import (  # noqa: F401
    # Layer 0–2: System prompts
    CORE_IDENTITY,
    FORBIDDEN_PHRASES,
    INTENT_CLASSIFIER_SYSTEM,
    PHASE1_DECISION_SYSTEM,
    SINGLE_PASS_STREAM_SYSTEM,
    PHASE2_STREAM_SYSTEM,
    NON_STREAMING_SYSTEM,
    MEMORY_OBSERVER_SYSTEM,
    # Layer 4: Dynamic builders
    build_phase1_user_prompt,
    format_resolved_song,
    build_memory_observer_messages,
    build_phase1_fail_user_prompt,
    # Tool constraints + fallback text
    TOOL_CONSTRAINTS,
    build_retry_feedback,
    GRACEFUL_FALLBACK_TEXT,
)

# ── Legacy alias — kept for code that still imports these names ──────────

# RFC-004 / RFC-006 era constants — re-export with old names
ENHANCED_SYSTEM_PERSONA = CORE_IDENTITY
ENHANCED_TOOL_CONSTRAINTS = TOOL_CONSTRAINTS

# Legacy intent classifier prompt (now expanded in INTENT_CLASSIFIER_SYSTEM)
INTENT_CLASSIFIER_SYSTEM_PROMPT = INTENT_CLASSIFIER_SYSTEM

# Legacy Phase 1 builder (old name)
build_phase1_decision_prompt = build_phase1_user_prompt

# Legacy memory observer exports (old names)
MEMORY_OBSERVER_SYSTEM_PROMPT = MEMORY_OBSERVER_SYSTEM
