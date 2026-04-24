# Aud.IO

Aud.IO is an AI voice assistant project with a FastAPI backend and a frontend UI.

## Project layout

- backend: FastAPI API, agent orchestration, memory context, tool wrappers
- frontend: UI app (Vue 3 or React + Nothing Design direction)
- docs: architecture notes and API docs

## Quick start

1. Create and activate a Python virtual environment.
2. Install backend dependencies:
   - pip install -r backend/requirements.txt
3. Copy env template:
   - copy backend/.env.example backend/.env
4. (Optional) Copy local memory templates:
   - copy backend/memory/taste.example.md backend/memory/taste.md
   - copy backend/memory/routines.example.md backend/memory/routines.md
5. Run API server:
   - uvicorn backend.main:app --reload --port 8000

## LLM environment strategy (for open source)

Use a provider-agnostic env schema in backend/.env.example:

- LLM_PROVIDER
- LLM_BASE_URL
- LLM_MODEL
- LLM_API_KEY

Why this is recommended:

- One stable format for DeepSeek, OpenAI, Anthropic, and future providers.
- Easier onboarding for contributors.
- Keep backend/.env local only; commit backend/.env.example only.

### DeepSeek local example

In backend/.env:

- LLM_PROVIDER=deepseek
- LLM_BASE_URL=https://api.deepseek.com
- LLM_MODEL=deepseek-chat
- LLM_API_KEY=your_real_deepseek_key

You can also set DEEPSEEK_API_KEY as a fallback when LLM_API_KEY is empty.

## Open-source safety checklist

- Never commit any API keys (Claude, Fish Audio, OpenAI, etc.).
- Keep real credentials only in local .env files.
- Commit only backend/.env.example as a template.
- Keep personal memory files local, such as backend/memory/taste.md.
- Before pushing, run git status and confirm no sensitive files are staged.

## Initial API routes

- GET /health
- GET /ready
- POST /v1/agent/respond

## Iteration plan

- Connect real LLM provider in backend/agent/llm_client.py
- Implement NetEase, weather, and TTS tools under backend/tools
- Build frontend pages and connect API
- Expand docs with diagrams and sequence flows
