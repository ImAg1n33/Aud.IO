# Aud.IO

Aud.IO is an AI voice assistant project with a FastAPI backend and a frontend UI.

## Project layout

- backend: FastAPI API, agent orchestration, memory context, tool wrappers
   - backend/api: route layer
   - backend/services: business orchestration layer
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

## Testing

1. Install dev dependencies:
   - pip install -r requirements-dev.txt
2. Run test suite:
   - pytest

Current test scope:

- CORS parsing behavior
- Agent route response contract
- Assistant service orchestration
- MemoryManager async JSON Patch update flow

## CI

GitHub Actions workflow is defined in:

- .github/workflows/ci.yml

It runs on push to main and pull requests, and executes:

1. pytest
2. scripts/security_scan.py

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

### CORS allowlist

- Configure `CORS_ALLOW_ORIGINS` in `backend/.env` as comma-separated origins.
- Default template value is local frontend dev origins only.

## Open-source safety checklist

- Never commit any API keys (Claude, Fish Audio, OpenAI, etc.).
- Keep real credentials only in local .env files.
- Commit only backend/.env.example as a template.
- Keep personal memory files local, such as backend/memory/taste.md.
- Before pushing, run git status and confirm no sensitive files are staged.

Detailed runbook:

- docs/security-playbook.md

Pre-push scanner:

- VS Code Task: Scan Secrets (Tracked Files)

Pre-commit protection:

- VS Code Task: Install Git Hooks (run once per clone)
- Hook file: .githooks/pre-commit

## Initial API routes

- GET /health
- GET /ready
- POST /v1/agent/respond
   - reply is a strict JSON object with keys:
      - analysis: string
      - answer: string
      - actions: string[]
      - provider: string
      - model: string

## Iteration plan

- Connect real LLM provider in backend/agent/llm_client.py
- Implement NetEase, weather, and TTS tools under backend/tools
- Build frontend pages and connect API
- Expand docs with diagrams and sequence flows
