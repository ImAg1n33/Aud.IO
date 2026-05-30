# Changelog

All notable changes to Aud.IO are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Centralized prompt registry (`prompts.py`) with layered architecture (RFC-007)
- Professional DJ + friend persona with forbidden broadcast phrases
- Hard play signal detection in intent classifier (eliminates song/emotion ambiguity)
- System prompts sent as `role="system"` for higher instruction adherence
- `not_found` status phase handling in frontend chat store

### Changed
- `call_llm()` and `stream_llm()` now accept `system_prompt` + `user_prompt` as separate args
- `ContextAssembler` no longer embeds system persona — only assembles user-prompt portion
- `prompt_builder.py` re-exports from centralized `prompts.py`
- Phase 1 decision no longer hallucinates artist names for bare song title queries

### Fixed
- PHASE2_STREAM_SYSTEM missing JSON format example causing empty DJ scripts
- f-string brace escaping in Phase 2 prompt preventing module import on CI

## [0.2.1] — 2026-05-24

- Architecture roadmap published

## [0.2.0] — 2026-05-20

### Added
- MCP protocol adapter layer (RFC-001)
- Two-Pass streaming architecture for music intents (RFC-003)
- Hybrid intent classifier: LLM primary + keyword fallback (RFC-004)
- Multi-user session isolation
- NetEase cookie expiry detection with auto-retry
- Unified HTTP client migration to httpx
- Environment context provider (weather + time of day) (RFC-005)
- Episodic memory vector search (ChromaDB semantic retrieval)
- User profile mood validation via Pydantic

### Changed
- App.vue split into ChatPanel, PlayerPanel, InputBar, DebugPanel
- Pinia stores activated for chat and player state
- `.env` loading unified to single entry point

### Removed
- Legacy `build_prompt()` function
- `frontend_backup/` directory
- All `urllib` dependencies

## [0.1.0] — 2026-05-11

### Added
- Initial architecture: FastAPI backend + Vue 3 frontend + Docker Compose
- ChromaDB + SQLite dual-write episodic memory
- SSE streaming with typewriter effect
- NetEase Cloud Music API integration
- Keyword-based intent classification
- Dark/light theme (Nothing Design)
