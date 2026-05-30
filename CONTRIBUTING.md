# Contributing to Aud.IO

Thanks for your interest in contributing. Aud.IO is an AI-powered music DJ — a smart radio that understands your mood, remembers your taste, and lets you chat naturally to discover and play music.

## Getting started

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose (optional, for full-stack deployment)
- A DeepSeek API key (or OpenAI / Anthropic compatible)

### Setup

```bash
# Clone and install backend dependencies
git clone https://github.com/ImAg1n33/Aud.IO.git
cd Aud.IO
pip install -r requirements-dev.txt

# Copy and edit environment config
cp backend/.env.example backend/.env
# Edit backend/.env — add your LLM_API_KEY at minimum

# Install frontend dependencies
cd frontend && npm install && cd ..

# Run tests
pytest
```

### Running locally

```bash
# Backend (with auto-reload)
cd backend && uvicorn main:app --reload --port 8001

# Frontend (dev server)
cd frontend && npm run dev
```

Or use Docker:
```bash
docker compose up -d --build
```

## Project structure

See the [README](README.md) for the full architecture diagram. Key directories:

```
backend/
  agent/          LLM orchestration, prompts, intent classification
  memory/         ChromaDB + SQLite episodic memory, user profiles
  tools/          Tool registry, MCP adapter, NetEase API
  services/       Session management, assistant pipeline
  api/            FastAPI routes
frontend/
  src/
    components/   ChatPanel, PlayerPanel, InputBar, DebugPanel
    stores/       Pinia stores (chat, player)
```

## Development workflow

1. **Fork** the repository and create a feature branch from `main`.
2. **Write tests** for any new functionality.
3. **Run the full suite** before pushing: `pytest`
4. **Keep commits focused** — one logical change per commit.
5. **PR description** should explain *why*, not just *what*.

### Commit style

Follow the existing convention:
- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `test:` — adding or updating tests
- `chore:` — tooling, CI, dependencies
- `docs:` — documentation changes

### Before submitting a PR

```bash
# Run tests
pytest

# The pre-commit hook runs a secret scanner automatically.
# If it catches anything, fix it before committing.
```

## Architecture decisions

Major changes are documented as RFCs in `docs/architecture-reports/`. If your change touches the agent pipeline, prompt system, memory architecture, or tool layer, consider writing a brief RFC first.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Please read it before participating.
