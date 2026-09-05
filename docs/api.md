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
| `context` | object | no | Frontend context (`Currently Playing`, `raw_context`) |
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

## POST /v1/agent/respond/stream

Streaming agent pipeline via **Server-Sent Events (SSE)**. Text tokens arrive in real-time for typewriter UI; music data triggers playback.

**Body** — same as `/respond`.

**SSE Event Types**:

| Event | Data | Description |
|-------|------|-------------|
| `status` | `{"phase":"searching"}` | Two-Pass Phase 1 started |
| `status` | `{"phase":"found","name":"...","artist":"..."}` | Song resolved, about to play |
| `status` | `{"phase":"not_found"}` | Song not found, DJ breaks the news |
| `music` | `{"song_id":"...","name":"...","artist":"...","mp3_url":"..."}` | Start playback immediately |
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
data: {"song_id":"123","name":"So What","artist":"Miles Davis","mp3_url":"http://..."}

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
