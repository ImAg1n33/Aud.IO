# Aud.IO Architecture

## Pipeline Overview

```
User Input
  │
  ▼
IntentClassifier ──→ Hard Signal (来一首/播放) → MUSIC_PLAY
  │                  LLM classify (1.5s timeout) → 5 intents
  │                  Keyword fallback (deterministic)
  │
  ▼
ContextAssembler — 6 pluggable Providers → user-prompt string
  │  Environment / History / Preference / CurrentlyPlaying / Tools / Episodic
  │
  ▼
LLM Call ──→ System Prompt (role="system", from prompts.py Layer 0-3)
  │          User Prompt (role="user", from ContextAssembler)
  │
  ├── MUSIC_PLAY ──→ Two-Pass Pipeline
  │     Phase 1: Silent pre-fetch (LLM extract keyword → NetEase search → MP3 resolve)
  │     Phase 2: Music starts → DJ script streams over intro
  │
  └── Other ──→ Single-Pass Pipeline
        SSE stream tokens → Execute tools → Yield music → Record
```

## Component Map

### Agent Layer (`backend/agent/`)

| File | Role |
|------|------|
| `prompts.py` | All LLM prompts, Layer 0-4 (Identity → Constraints → Task → Output Schema → Context) |
| `intent_classifier.py` | Hybrid intent: Hard Play Signals → LLM (1.5s) → Keyword deterministic |
| `context_assembler.py` | 6 ContextProvider plugins assemble user-prompt dynamically |
| `llm_client.py` | httpx async backend, `call_llm()` + `stream_llm()`, system role separation |
| `tool_executor.py` | Tool dispatch, copyright retry loop (max 2) |
| `memory_manager.py` | User profile JSON Patch updates via background LLM observer |

### Memory Layer (`backend/memory/`)

Repository pattern (RFC-008) — 7 files, max 409 lines:

| File | Lines | Role |
|------|-------|------|
| `episodic_memory.py` | 409 | **Facade** — orchestrates write/read/decay rerank/fallback |
| `_sqlite_repo.py` | 294 | **SqliteRepository** — pure SQLite CRUD |
| `_chroma_repo.py` | 244 | **ChromaRepository** — pure vector store operations |
| `_migration.py` | 159 | **MigrationManager** — versioned, idempotent (v1: session_id, v2: decay fields) |
| `models.py` | 114 | EpisodicSnapshot, _Meta, _row_to_snapshot, time utils |
| `mood_detector.py` | 111 | Chinese/English keyword → 10 mood tags |
| `decay.py` | 52 | Pure function: Ebbinghaus-weighted retrieval score |

**Write path**: `store_snapshot()` → SqliteRepository.insert → ChromaRepository.upsert (ChromaDB failure is non-blocking)

**Read path**: `query_by_semantic()` → ChromaRepository.semantic_search → SqliteRepository.load_decay_fields → compute_decayed_score rerank → SqliteRepository.record_access

**Fallback**: ChromaDB unavailable → SqliteRepository LIKE search

**Dual-write ID sharing**: SQLite AUTOINCREMENT ID is used as ChromaDB document ID, ensuring cross-store traceability.

### Tool Layer (`backend/tools/`)

| File | Role |
|------|------|
| `base.py` | `BaseTool`, `ToolRegistry` singleton, error hierarchy (`ToolError` → `MusicCopyrightError`, `CookieExpiredError`, etc.) |
| `music_tool.py` | `search_music` / `get_music_url` tools |
| `netease_api.py` | NetEase API wrapper: cookie expiry detection, transient error retry (max 2) |
| `mcp_adapter.py` | MCP Client (RFC-001): stdio transport, external tool discovery, `MCPToolAdapter` wrapping |
| `login_netease.py` | QR-code login flow |

### Services (`backend/services/`)

| File | Role |
|------|------|
| `assistant_service.py` | **Core orchestrator** — Perceive→Decide→Execute→Record pipeline |
| `session_manager.py` | TTL-based per-user session pool (24h idle eviction, max 100 sessions) |

## Request Flow Details

### Two-Pass (MUSIC_PLAY)

```
1. Intent = MUSIC_PLAY
2. SSE "searching" status → frontend shows "Searching..."
3. Phase 1: call_llm(PHASE1_DECISION_SYSTEM) → extract play_keyword
4. NetEase search_first_song(play_keyword) → get_song_mp3_url(id)
5. SSE "found" status + "music" event → frontend starts playback
6. Phase 2: stream_llm(PHASE2_STREAM_SYSTEM, resolved_song) → DJ script over intro
7. SSE "done" → record in memory
```

### Single-Pass (CHITCHAT / WEATHER / UNKNOWN / MUSIC_RECOMMEND)

```
1. Intent ≠ MUSIC_PLAY
2. ContextAssembler builds user-prompt with relevant providers
3. stream_llm(SINGLE_PASS_STREAM_SYSTEM, user_prompt) → SSE tokens
4. Parse actions from LLM response → ToolExecutor.execute
5. Copyright error? → retry (non-streaming LLM, max 2)
6. SSE "music" if song found → SSE "done" → record in memory
```

## Prompt Architecture (RFC-007)

5 LLM call types, each with pre-built SYSTEM prompt:

| Call | System Constant | Role |
|------|----------------|------|
| Intent classify | `INTENT_CLASSIFIER_SYSTEM` | Ultra-lean, JSON-only |
| Phase 1 decision | `PHASE1_DECISION_SYSTEM` | Song/emotion disambiguation |
| Phase 2 DJ script | `PHASE2_STREAM_SYSTEM` | "Hitting the Post" timing |
| Single-pass streaming | `SINGLE_PASS_STREAM_SYSTEM` | Full conversation + tools |
| Memory observer | `MEMORY_OBSERVER_SYSTEM` | JSON Patch profile update |

Each SYSTEM prompt layers: `CORE_IDENTITY` → `FORBIDDEN_PHRASES` → Task instructions → Output schema. The user-prompt (role="user") carries dynamic context only.

## Memory Decay Formula

```
score = semantic_sim × 0.50    (ChromaDB cosine similarity)
      + importance   × 0.20     (0.3 chat / 0.6 mood / 0.8 play)
      + freshness    × 0.20     (e^(-hours_since_access / 168), ~7 day half-life)
      + access_bonus × 0.10     (log(access_count + 1) / log(11))
```

Values clamped to [0.0, 1.0]. Applied in `query_by_semantic()` as post-retrieval rerank.

## Database Migrations

`MigrationManager` with `schema_version` table:

```
v0: CREATE TABLE episodes (...) + indexes
v1: ALTER TABLE ADD COLUMN session_id  (multi-user isolation)
    + ChromaDB metadata backfill
v2: ALTER TABLE ADD COLUMN importance_score / access_count / last_accessed
v3: ALTER TABLE ADD COLUMN song_id + feedback fields (played_to_completion, listen_duration, play_count, skip_count, last_feedback)
v4: Self-healing repair — ensures all expected columns exist (historic DBs with incomplete migration state)
v5: session_summaries table (Reflection cross-session memory)
```

Migrations run on `EpisodicMemory.__init__()`, are idempotent, and execute in version order.

## Deployment Constraints

> ⚠️ **不要用多 worker 跑 uvicorn**（`--workers 2+` / `--reload` 仅限单进程开发模式）。
> 内嵌 ChromaDB PersistentClient 对同一数据目录持文件锁，多进程并发会锁冲突崩溃。
> 当前架构设计为**单进程单用户**（`routes_agent.py` 的 import-time 单例 + 内存 TTLCache 会话池），
> 需要多用户/多进程时再引入外部向量库（pgvector/Qdrant）与服务化会话存储。
