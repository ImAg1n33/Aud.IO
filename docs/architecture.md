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
  ├── PREV_SIGNALS (上一首) ──→ 播放历史回放 (prev_history，不经 LLM 选曲)
  │
  ▼
ContextAssembler — 7 pluggable Providers → user-prompt string
  │  Environment / History / SessionSummary / Preference / CurrentlyPlaying(+song_id) / Tools / Episodic
  │
  ▼
LLM Call ──→ System Prompt (role="system", from prompts.py Layer 0-3)
  │          User Prompt (role="user", from ContextAssembler)
  │
  ├── MUSIC_PLAY ──→ Two-Pass Pipeline
  │     Phase 1: Silent pre-fetch (LLM extract keyword → scored search → MP3 resolve)
  │             失败 → 降级到 Single-Pass 容错链（不道歉死胡同）
  │     Phase 2: Music starts → DJ script streams over intro
  │
  └── Other ──→ Single-Pass Pipeline
        SSE stream tokens → Execute tools → Yield music → Record
        四层容错链: 工具调用 → 严格重试 → Phase-1 确定性兜底 → Phase-2 台词
        （版权锁定 / 仅翻唱无原生版 → 换曲重试）
```

**Search quality scoring** (v0.7): `search_first_song` 拉取候选集（cloudsearch 优先），按
`originCoverType`（原唱/翻唱）、劣质标记、艺人名匹配、热度评分选优；全翻唱 →
`NoAcceptableSongError` → `MusicUnavailableError`（可重试换曲）。**Disliked enforcement**:
播放前校验画像 `disliked` 艺人 → 带反馈重试换艺人，仍命中则不播放。

## Component Map

### Agent Layer (`backend/agent/`)

| File | Role |
|------|------|
| `prompts.py` | All LLM prompts, Layer 0-4 (Identity → Constraints → Task → Output Schema → Context) incl. reflection + observer signal rules |
| `intent_classifier.py` | Hybrid intent: Hard Play Signals → LLM (1.5s) → Keyword deterministic |
| `context_assembler.py` | 7 ContextProvider plugins assemble user-prompt dynamically |
| `llm_client.py` | httpx async backend, `call_llm()` + `stream_llm()`, native function calling, thinking-disable, structured call log |
| `tool_executor.py` | Tool dispatch, retryable retry loop (copyright / no-original → change song, max 2) |
| `memory_manager.py` | User profile JSON Patch updates (observer w/ session summaries), deterministic dislike writes, audit log |

### Memory Layer (`backend/memory/`)

Repository pattern (RFC-008) — Facade + repositories + helpers:

| File | Role |
|------|------|
| `episodic_memory.py` | **Facade** — orchestrates write/read/hybrid-retrieval/decay rerank/fallback/feedback/reflection |
| `_sqlite_repo.py` | **SqliteRepository** — SQLite CRUD, feedback fields, summaries, play history, hybrid keyword search |
| `_chroma_repo.py` | **ChromaRepository** — pure vector store operations (dim metadata) |
| `_migration.py` | **MigrationManager** — versioned, idempotent (v1-v6, self-healing) |
| `models.py` | EpisodicSnapshot + `_row_to_snapshot` (**column-name based**, order-independent), time utils |
| `mood_detector.py` | Chinese/English keyword → 10 mood tags |
| `decay.py` | Pure function: Ebbinghaus-weighted retrieval score |
| `fusion.py` | RRF fusion (semantic + keyword legs) |
| `reflection.py` | SessionReflector — LLM session summaries (cross-session memory) |
| `embedding.py` | FastEmbed (BGE, persistent cache_dir) / ONNX / API providers |

**Write path**: `store_snapshot()` → SqliteRepository.insert → ChromaRepository.upsert (ChromaDB failure is non-blocking)

**Read path**: `query_by_semantic()` → ChromaRepository.semantic_search → SqliteRepository.load_decay_fields → compute_decayed_score rerank → SqliteRepository.record_access

**Fallback**: ChromaDB unavailable → SqliteRepository LIKE search

**Dual-write ID sharing**: SQLite AUTOINCREMENT ID is used as ChromaDB document ID, ensuring cross-store traceability.

### Tool Layer (`backend/tools/`)

| File | Role |
|------|------|
| `base.py` | `BaseTool`, `ToolRegistry` singleton, error hierarchy (`ToolError` → `MusicCopyrightError`, `MusicUnavailableError` (no original version), `CookieExpiredError`, etc.) |
| `music_tool.py` | `search_music` / `get_music_url` / `find_similar` (music-relationship data) tools |
| `netease_api.py` | NetEase API wrapper: search quality scoring, simi (similar songs), cookie expiry detection, transient error retry (max 2) |
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
4. Scored search (cloudsearch candidates → cover/original/artist/popularity scoring)
   → get_song_mp3_url(id); disliked artist check
5. SSE "found" status + "music" event → frontend starts playback
6. Phase 2: stream_llm(PHASE2_STREAM_SYSTEM, resolved_song) → DJ script over intro
7. SSE "done" → record in memory
   Phase 1 miss → fall through to Single-Pass fallback chain (never a dead-end apology)
```

### Single-Pass (CHITCHAT / WEATHER / UNKNOWN / MUSIC_RECOMMEND / skip / phase-1 miss)

```
1. Intent ≠ MUSIC_PLAY (or skip signal / phase-1 miss)
2. ContextAssembler builds user-prompt with relevant providers
3. stream_llm(SINGLE_PASS_STREAM_SYSTEM, user_prompt, tools) → SSE tokens
   (music intents: silent prefetch — model text buffered, not streamed)
4. Parse tool_calls → ToolExecutor.execute
5. No tool call? → strict retry → Phase-1 deterministic fallback (JSON keyword → search)
6. Copyright / no-original-version? → retry with a different song (non-streaming, max 2)
7. Disliked artist? → retry with different artist; still blocked → drop music
8. SSE "music" if resolved → Phase 2 DJ line → SSE "done" → record in memory
```

### Previous-Song Replay (上一首, v0.7)

```
1. PREV_SIGNALS matched → query_recent_played(session_id)
2. Pick most recent entry whose song_id != current (Currently Playing ID)
3. Resolve mp3 → SSE "music" → Phase 2 DJ line → done (trace path: prev_history)
   无历史 → 回落正常流程
```

## Prompt Architecture (RFC-007)

6 LLM call types, each with pre-built SYSTEM prompt:

| Call | System Constant | Role |
|------|----------------|------|
| Intent classify | `INTENT_CLASSIFIER_SYSTEM` | Ultra-lean, JSON-only |
| Phase 1 decision | `PHASE1_DECISION_SYSTEM` | Song/emotion disambiguation + genre→famous-song rule |
| Phase 2 DJ script | `PHASE2_STREAM_SYSTEM` | "Hitting the Post" timing (also used for replay) |
| Single-pass streaming | `SINGLE_PASS_STREAM_SYSTEM` | Full conversation + tools; no fabricated failures |
| Reflection summary | `SUMMARY_REFLECTION_SYSTEM` | Session summary (summary/topics/song_signals) |
| Memory observer | `MEMORY_OBSERVER_SYSTEM` | JSON Patch profile update; genre-only core_taste; explicit-preference signal rules; cross-session dedupe |

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
v6: ALTER TABLE ADD COLUMN dislike_count (explicit dislike / rejection learning)
```

Migrations run on `EpisodicMemory.__init__()`, are idempotent, and execute in version order.

## Deployment Constraints

> ⚠️ **不要用多 worker 跑 uvicorn**（`--workers 2+` / `--reload` 仅限单进程开发模式）。
> 内嵌 ChromaDB PersistentClient 对同一数据目录持文件锁，多进程并发会锁冲突崩溃。
> 当前架构设计为**单进程单用户**（`routes_agent.py` 的 import-time 单例 + 内存 TTLCache 会话池），
> 需要多用户/多进程时再引入外部向量库（pgvector/Qdrant）与服务化会话存储。
