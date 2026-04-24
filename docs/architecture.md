# Architecture Draft

## Components

- Frontend UI
- FastAPI backend
- Agent layer (prompt assembly + model routing)
- Memory context files (taste, routines)
- Tool adapters (music, weather, TTS)

## Data flow

1. User sends request from frontend.
2. Backend builds prompt from input + memory context.
3. Agent calls model and optional tools.
4. Backend returns structured response to frontend.
