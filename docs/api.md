# API Draft

## Health

- GET /health
  - response: {"status": "ok"}

## Ready

- GET /ready
  - response: {"ready": true}

## Agent Respond

- POST /v1/agent/respond
  - body:
    - user_input: string
    - context: object (optional)
  - response:
    - reply: string
    - prompt: string
