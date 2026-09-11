# Aud.IO API

Base URL: `http://localhost:8001`

> **Looking for the full field-by-field reference?**
> FastAPI generates it from the code automatically — start the backend and open
> <http://localhost:8001/docs> (Swagger) or <http://localhost:8001/redoc>.
> This page documents what a generated schema can't express: what each endpoint is *for*,
> the SSE event sequence, and the feedback calibration rules.

---

## GET /health

Health check.

**Response** `200`:
```json
{"status": "ok"}
```

---

## GET /ready

Readiness check — reports whether each subsystem is configured and reachable.
This is the first thing to check when the app starts but behaves oddly.

**Response** `200`:

| Field | Type | Description |
|-------|------|-------------|
| `ready` | boolean | Overall readiness flag |
| `version` | string | Running application version |
| `llm` | object | `{provider, model, base_url, configured}` — the active LLM backend |
| `embedding` | object | `{provider}` — one of `local` / `fastembed` / `api` (see `EMBEDDING_PROVIDER`) |
| `chromadb` | object | `{ok, documents}` — vector store reachability and stored episode count |
| `netease` | object | `{configured}` — whether the music API base URL or cookie is set |
| `mcp` | object | `{servers, tools}` — connected MCP servers and adapted tools |
| `metrics` | object | `{llm_calls_24h: {total, failed, avg_latency_ms}}` — rolling 24h LLM call stats |

These values depend on your `.env`, so they differ per deployment. The live endpoint is the
authoritative source — prefer it over any example in this file.

---

## POST /v1/agent/respond

Non-streaming agent pipeline. Full response returned at once.

**Body** (JSON):
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_input` | string | yes | User's raw message |
| `context` | object | no | Frontend context (`Currently Playing`, `Currently Playing ID`, `Auto Continue`) |
| `session_id` | string | no | UUID session identifier (auto-generated if absent) |

**Response** `200`:
```json
{
  "reply": {
    "analysis": "brief reasoning",
    "answer": "user-facing response text",
    "actions": [],
    "play_keyword": "Artist SongTitle",
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "music": {
      "requested_keyword": "Artist SongTitle",
      "song_id": "12345",
      "name": "Song Name",
      "artist": "Artist",
      "cover_url": "http://p2.music.126.net/....jpg",
      "album": "Album Name",
      "duration_ms": 213266,
      "mp3_url": "http://..."
    }
  },
  "prompt": "<assembled prompt sent to LLM>"
}
```

---

## POST /v1/agent/feedback

Playback feedback reporting — the frontend reports what actually happened to a track
(finished / skipped / failed), and the backend calibrates the corresponding memory
snapshot's `importance_score`. This is how the DJ learns from real listening behavior.

**Body** (JSON):
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | `song_started` / `song_finished` / `song_skipped` / `song_disliked` / `song_failed` |
| `song_id` | string | yes | Song ID from the `music` SSE event |
| `session_id` | string | no | UUID session identifier (same as agent endpoints) |
| `listen_seconds` | number | no | Seconds listened (reported on finished/skipped) |

**Effects**:
- `song_finished` → `played_to_completion=1`, `play_count+1`, `importance_score +0.15`
- `song_skipped` → `skip_count+1`, `importance_score -0.15`
- `song_disliked` → `dislike_count+1`, `importance_score -0.30` + artist written into
  the profile's `disliked` list (deterministic, no LLM interpretation) — future
  recommendations avoid this artist
- `song_started` / `song_failed` → mark `last_feedback` only (no weight change)

**Response** `200`:
```json
{"ok": true, "matched_snapshot_id": 42, "disliked_artist": null}
```
`matched_snapshot_id` is the memory snapshot the event was matched to
(`null` when the song was never recorded). `disliked_artist` is set only for
`song_disliked` when the artist was newly written into the profile.

**Errors**: `400` illegal `session_id` · `422` invalid `event` / missing `song_id`.

---

## GET /v1/agent/history

Play history (RFC-020). Every emitted `music` event is recorded with per-session
dedup; a session keeps its latest 50 tracks, and the global list keeps the latest 100.

**Query parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `scope` | string | `global` | `session` (this session's playlist cache) or `global` (cross-session recent) |
| `limit` | int | `50` | Max rows returned (capped at 100) |
| `session_id` | string | — | Required when `scope=session` |

**Response** `200`:
```json
{
  "scope": "session",
  "songs": [
    {
      "song_id": "82453",
      "name": "爱爱爱",
      "artist": "方大同",
      "cover_url": "http://p2.music.126.net/....jpg",
      "album": "爱爱爱",
      "duration_ms": 213266,
      "requested_keyword": "方大同 爱爱爱",
      "played_at": "2026-09-11T09:00:00Z",
      "session_id": "...."
    }
  ]
}
```

**Errors**: `400` missing/illegal `session_id` when `scope=session`.

Notes: rows carry metadata only — `mp3_url` is intentionally never cached
(NetEase URLs expire); use `/replay` to re-resolve before playback.

---

## POST /v1/agent/replay

Re-resolve a song from play history and return a playable payload (RFC-020).
Used by the frontend for list-mode local switching and history-panel playback —
no LLM involved. Replaying also moves the song to the top of the history.

**Body** (JSON):
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `song_id` | string | yes | Song ID (must be present in play history) |
| `session_id` | string | no | UUID session identifier (falls back to global lookup) |

**Response** `200`:
```json
{
  "song_id": "82453",
  "name": "爱爱爱",
  "artist": "方大同",
  "cover_url": "http://p2.music.126.net/....jpg",
  "album": "爱爱爱",
  "duration_ms": 213266,
  "requested_keyword": "previous",
  "mp3_url": "http://m8.music.126.net/....mp3"
}
```

**Errors**: `400` illegal `session_id` · `404` song not in history ·
`502` NetEase could not resolve a playable URL (copyright/regional lock).

---

## POST /v1/agent/respond/stream

Streaming agent pipeline via **Server-Sent Events (SSE)**. Text tokens arrive in real-time for typewriter UI; music data triggers playback.

**Body** — same as `/respond`.

**Auto-Continue (DJ radio mode)**: send `context: {"Auto Continue": "true"}` with an
empty `user_input` to request the next track for continuous playback. The backend
picks via similar-song data first (LLM taste-based fallback), excludes the session's
last 10 tracks, and streams the normal `music` + `token` + `done` sequence
(trace path `auto_dj`). If nothing is available it emits an `error` event.

**SSE Event Types**:

| Event | Data | Description |
|-------|------|-------------|
| `status` | `{"phase":"searching"}` | Two-Pass Phase 1 started |
| `status` | `{"phase":"found","name":"...","artist":"..."}` | Song resolved, about to play |
| `status` | `{"phase":"not_found"}` | Song not found, DJ breaks the news |
| `music` | `{"song_id":"...","name":"...","artist":"...","cover_url":"...","album":"...","duration_ms":213266,"mp3_url":"..."}` | Start playback immediately |
| `speech` | `{"urls":["https://..."], "text":"...", "intent":"chitchat"}` | TTS audio URLs for voice output |
| `token` | `"char"` | Single displayable character (typewriter feed) |
| `text` | `"full assembled answer"` | Complete answer text (after streaming) |
| `done` | `{"analysis":"...","answer":"...","music":{...}}` | Final structured reply (debug panel) |
| `error` | `"error message"` | LLM call failed or stream interrupted |

**Response** `200`:
```
Content-Type: text/event-stream

event: status
data: {"phase":"searching"}

event: status
data: {"phase":"found","name":"So What","artist":"Miles Davis"}

event: music
data: {"song_id":"123","name":"So What","artist":"Miles Davis","cover_url":"http://p2.music.126.net/....jpg","album":"Kind of Blue","duration_ms":545000,"mp3_url":"http://..."}

event: token
data: P

event: token
data: u

event: token
data: t

event: text
data: Put on some Miles Davis...

event: done
data: {"analysis":"...","answer":"Put on some Miles Davis...","music":{...}}
```
