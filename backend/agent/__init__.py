from backend.agent.context_assembler import (
    ContextAssembler,
    ContextProvider,
    ConversationHistoryProvider,
    CurrentlyPlayingProvider,
    EnvironmentProvider,
    EpisodicMemoryProvider,
    ToolSchemaProvider,
    UserPreferenceProvider,
)
from backend.agent.intent_classifier import Intent, IntentClassifier
from backend.agent.llm_client import call_llm, request_json_object, stream_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompt_builder import (  # backward-compat re-exports
    ENHANCED_SYSTEM_PERSONA,
    ENHANCED_TOOL_CONSTRAINTS,
    INTENT_CLASSIFIER_SYSTEM_PROMPT,
    build_memory_observer_messages,
    build_phase1_decision_prompt,
    format_resolved_song,
)
from backend.agent.prompts import (  # canonical source (RFC-007)
    CORE_IDENTITY,
    FORBIDDEN_PHRASES,
    GRACEFUL_FALLBACK_TEXT,
    INTENT_CLASSIFIER_SYSTEM,
    MEMORY_OBSERVER_SYSTEM,
    NON_STREAMING_SYSTEM,
    PHASE1_DECISION_SYSTEM,
    PHASE2_STREAM_SYSTEM,
    SINGLE_PASS_STREAM_SYSTEM,
    TOOL_CONSTRAINTS,
    build_phase1_fail_user_prompt,
    build_phase1_user_prompt,
    build_retry_feedback,
)
from backend.agent.tool_executor import ToolExecutor

__all__ = [
    # LLM
    "call_llm",
    "request_json_object",
    "stream_llm",
    # Memory
    "MemoryManager",
    # Intent
    "Intent",
    "IntentClassifier",
    # Context assembly
    "ContextAssembler",
    "ContextProvider",
    "ConversationHistoryProvider",
    "UserPreferenceProvider",
    "CurrentlyPlayingProvider",
    "ToolSchemaProvider",
    "EpisodicMemoryProvider",
    "EnvironmentProvider",
    # Tool execution
    "ToolExecutor",
    # Prompts — new canonical (RFC-007)
    "CORE_IDENTITY",
    "FORBIDDEN_PHRASES",
    "INTENT_CLASSIFIER_SYSTEM",
    "PHASE1_DECISION_SYSTEM",
    "PHASE2_STREAM_SYSTEM",
    "SINGLE_PASS_STREAM_SYSTEM",
    "NON_STREAMING_SYSTEM",
    "MEMORY_OBSERVER_SYSTEM",
    "TOOL_CONSTRAINTS",
    "GRACEFUL_FALLBACK_TEXT",
    "build_phase1_user_prompt",
    "build_phase1_fail_user_prompt",
    "build_retry_feedback",
    # Prompts — backward compat (RFC-004 / RFC-006 era)
    "build_memory_observer_messages",
    "ENHANCED_SYSTEM_PERSONA",
    "ENHANCED_TOOL_CONSTRAINTS",
    # RFC-003 Two-Pass (backward compat)
    "build_phase1_decision_prompt",
    "format_resolved_song",
    # RFC-004 Intent Classifier (backward compat)
    "INTENT_CLASSIFIER_SYSTEM_PROMPT",
]
