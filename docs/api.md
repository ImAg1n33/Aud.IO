# Aud.IO API

Base URL: `http://localhost:8001`

---

## GET /health

Health check.

**Response** `200`:
```json
{"status": "ok"}
```

---

## GET /ready

Readiness check with configuration status.

**Response** `200`:
```json
{
  "ready": true,
  "version": "0.3.1",
  "llm": {"provider": "deepseek", "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com", "configured": true},
  "embedding": {"provider": "onnx"},
  "chromadb": {"ok": true, "documents": 42},
  "netease": {"configured": true},
  "mcp": {"servers": 0, "tools": 0}
}
```

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
