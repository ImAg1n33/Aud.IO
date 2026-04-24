import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _get_llm_config() -> dict[str, str]:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
        elif provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        elif provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = stripped[start : end + 1]
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Model output is not a valid JSON object.")


def _normalize_response(payload: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    analysis = payload.get("analysis", "")
    answer = payload.get("answer", "")
    actions = payload.get("actions", [])
    play_keyword = payload.get("play_keyword", "")

    if not isinstance(analysis, str):
        analysis = str(analysis)
    if not isinstance(answer, str):
        answer = str(answer)
    if not isinstance(actions, list):
        actions = [str(actions)]
    actions = [str(item) for item in actions]
    if not isinstance(play_keyword, str):
        play_keyword = str(play_keyword)

    return {
        "analysis": analysis,
        "answer": answer,
        "actions": actions,
        "play_keyword": play_keyword.strip(),
        "provider": provider,
        "model": model,
    }


def call_llm(prompt: str) -> dict[str, Any]:
    """Call DeepSeek (OpenAI-compatible API) and return strict JSON.

    Response schema:
    - analysis: brief reasoning summary
    - answer: final user-facing answer
    - actions: next steps list
    - play_keyword: required only when user intent is to play music
    """
    config = _get_llm_config()

    if not config["api_key"]:
        return {
            "analysis": "Missing API key.",
            "answer": "LLM_API_KEY is not configured.",
            "actions": ["Set backend/.env with LLM_API_KEY and retry."],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }

    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Aud.IO's reasoning core. Think based on user input and return "
                    "strict JSON only with keys: analysis (string), answer (string), "
                    "actions (string array), play_keyword (string). "
                    "If user asks to play/search a song, play_keyword must be a concrete "
                    "music search phrase. Otherwise set play_keyword to an empty string."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    req = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
        completion = json.loads(raw)
        content = completion["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        return _normalize_response(parsed, config["provider"], config["model"])
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "analysis": "Model call failed.",
            "answer": "DeepSeek request failed, please check network/key/model settings.",
            "actions": [str(exc)],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }
