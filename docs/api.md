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
    - reply: object
      - analysis: string
      - answer: string
      - actions: string[]
      - play_keyword: string
      - provider: string
      - model: string
      - music: object (optional)
        - requested_keyword: string
        - song_id: string
        - name: string
        - artist: string
        - mp3_url: string
    - prompt: string
