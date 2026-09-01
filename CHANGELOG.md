# Changelog

All notable changes to Aud.IO are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.5.0] — 2026-09-01

### Added
- **会话级 trace 日志（Agent Observability）**：每次交互写入 `data/conversations.jsonl`
  （意图/路径/工具调用/音乐/回答/延迟/错误），`scripts/view_conversations.py` 人性化查看——
  与 `llm_calls.jsonl`（LLM 级）构成双层行为留存，便于复盘与调试
- **Reflection 会话摘要（v5）** — 跨会话连续性：
  - 每 10 轮对话自动触发 LLM 摘要（`SUMMARY_REFLECTION_SYSTEM`），产出
    {summary, topics, song_signals} 结构化入库（`session_summaries` 表）
  - `SessionSummaryProvider` 把历史会话摘要注入上下文——DJ 重启/换会话不失忆
  - 触发节流（`ctx.last_summary_turn`）、失败静默降级，不阻断对话
- Hybrid retrieval in `query_by_semantic`: semantic (ChromaDB) + keyword (SQLite LIKE on
  input/reply/song name/artist) fused via RRF (k=60), then decay rerank; ChromaDB-down
  still falls back to pure keyword
- `FastEmbedProvider` (fastembed): local Chinese-optimized BGE embeddings
  (`EMBEDDING_PROVIDER=fastembed`, `BAAI/bge-small-zh-v1.5` default)
- `scripts/rebuild_embeddings.py`: rebuild ChromaDB collection when switching embedding
  models (dimension detection + auto delete/recreate, batch upsert)
- Playback feedback loop: `POST /v1/agent/feedback` receives song_started/finished/skipped/failed events
- Frontend playback telemetry: `PlayerPanel` reports finish/skip/fail to calibrate memory importance
- Memory calibration: `record_play_feedback()` adjusts `importance_score` (+0.15 finished / -0.15 skipped, clamped 0.05-0.98)
- Migration v3: `song_id` + feedback columns (`played_to_completion`, `listen_duration`, `play_count`, `skip_count`, `last_feedback`)
- Eval baseline: intent golden set (60 cases) + retrieval eval set (12 seeds / 11 queries) with runners (`eval/`)
- Centralized prompt registry (`prompts.py`) with layered architecture (RFC-007)

### Changed
- **前端界面重设计（v0.6 体验）**：
  - 对话区升级为**气泡消息流**：用户/DJ 消息历史保留可回看，打字机只作用于最新消息
  - PREV/NEXT 实装：NEXT=快捷指令"换一首"（走 agent 通道并触发切歌反馈），PREV=重播当前曲
  - MODE 实装：dj / loop（`audio.loop` 循环）/ list；音量滑条（本地记忆）+ 进度条点击跳转
  - 播放状态可视化：缓冲指示点、加载态、错误块
  - 空态引导 + 键盘快捷键（Ctrl+K 或 / 聚焦输入、空格播放/暂停）
  - 会话标识内部化（chat store），移除 props 透传链
  - chat.js 消息流逻辑抽为纯 reducer（Node 可直接测试，+8 用例）
- **RFC: function calling 重构** — 移除 `---JSON---` 文本标记协议（`llm_client` / prompts）：
  - `call_llm` / `stream_llm` 支持 OpenAI 原生 `tools` 参数，tool_calls 归一化为
    `{"tool": name, ...args}` 动作 dict（流式分片自动拼接 arguments）
  - `_parse_actions_from_reply` 删除 json.loads/ast 兜底解析链（仅保留类型过滤）
  - `BaseTool.category` 意图门控：MUSIC_PLAY/RECOMMEND → 音乐工具；CHITCHAT/WEATHER → 无工具；
    UNKNOWN → 全部可用工具
  - 流式输出为纯文案（前端打字机直出），工具调用走标准协议，不再与文本混编
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
- **deepseek-v4-flash 空回答**: 该模型是推理模型，`reasoning_content` 会吃光 `max_tokens`
  预算导致 `content` 为空。新增 `_provider_extra_body()` 对 deepseek 显式
  `thinking: {"type": "disabled"}`（`LLM_DISABLE_THINKING=false` 可关），
  意图分类/流式回答/JSON 调用全部修复
- PHASE2_STREAM_SYSTEM missing JSON format example causing empty DJ scripts
- f-string brace escaping in Phase 2 prompt preventing module import on CI

## [0.3.1] — 2026-06-12

### Fixed
- `.env` loading order: `load_dotenv()` now runs before all backend imports, ensuring `AUD_IO_DATA_DIR`, `EMBEDDING_PROVIDER`, `MEMORY_MODEL` etc. take effect during service initialization
- Illegal `session_id` now returns HTTP 400 instead of 500

### Added
- `normalize_session_id()` input validation (accepts UUID / safe slug, rejects path traversal)
- `SSEParser` state machine for cross-chunk SSE event resilience
- `/ready` endpoint expanded with LLM, Embedding, ChromaDB, NetEase, MCP status fields

### Changed
- Runtime data directory moved from `backend/memory/` to `backend/data/` (`AUD_IO_DATA_DIR` env var)
- `ContextAssembler.assemble()` passes `session_id` to all memory providers (session-scoped recall)
- Version string corrected: `0.2.0` → `0.3.1`
- Removed stale `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` references (Anthropic Messages API not supported)

### Security
- `session_id` validated at API boundary against path traversal
- Runtime data (episodes, profiles, chroma) isolated from source tree in `backend/data/`

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
