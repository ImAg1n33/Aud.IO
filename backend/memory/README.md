# Local memory files

This folder stores local, user-specific context for the agent.

## Runtime-updated files

- user_profile.json: updated by MemoryManager background worker
- memory_update.log: audit trail for memory update status

## Keep local only (do not commit)

- taste.md
- routines.md

Note:

- taste.md and routines.md are manual/local notes and are not auto-updated by the current memory pipeline.

## Commit-safe templates

- taste.example.md
- routines.example.md

Copy templates for local development:

- copy backend/memory/taste.example.md backend/memory/taste.md
- copy backend/memory/routines.example.md backend/memory/routines.md
