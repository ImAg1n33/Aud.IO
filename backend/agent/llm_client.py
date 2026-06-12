"""LLM client — unified httpx async backend for both streaming and non-streaming calls.

v0.4 (RFC-007): System prompt sent as role="system" (not concatenated into user message).
Callers pass system_prompt and user_prompt separately.
"""

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _get_llm_config(model_override: str | None = None) -> dict[str, str]:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    configured_model = os.getenv("LLM_MODEL", "").strip()
    model = model_override.strip() if isinstance(model_override, str) and model_override.strip() else configured_model
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        # Provider-specific fallback keys — only for OpenAI-compatible APIs.
        # Anthropic uses the Messages API (not /chat/completions) and is not
        # supported by this client.  Use LLM_API_KEY for all providers.
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
        elif provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


async def _request_chat_json(
    config: dict[str, str],
    messages: list[dict[str, str]],
    temperature: float,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Async JSON-mode chat completion via httpx."""
    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            endpoint,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
            },
        )
        response.raise_for_status()
        completion = response.json()
    content = completion["choices"][0]["message"]["content"]
    return _extract_json(content)


async def request_json_object(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Generic JSON-object chat call for shared LLM backends.

    Raises when request or parsing fails.
    """
    config = _get_llm_config(model_override=model)
    if not config["api_key"]:
        raise RuntimeError("LLM_API_KEY (or provider fallback key) is not configured.")
    return await _request_chat_json(config, messages, temperature)


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


# ============================================================
# Non-streaming call
# ============================================================

async def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Call OpenAI-compatible API with system role and return structured JSON.

    Args:
        system_prompt: System-level instructions (identity, task, output format).
                       Sent as role="system" for higher instruction adherence.
        user_prompt: Context blocks + user input. Sent as role="user".

    Response schema:
    - analysis: brief reasoning summary
    - answer: final user-facing answer
    - actions: next steps list
    - play_keyword: "Artist SongTitle" or empty string
    """
    config = _get_llm_config(model_override=model)

    if not config["api_key"]:
        return {
            "analysis": "Missing API key.",
            "answer": "LLM_API_KEY is not configured.",
            "actions": ["Set backend/.env with LLM_API_KEY and retry."],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        parsed = await _request_chat_json(config, messages, temperature=0.2)
        return _normalize_response(parsed, config["provider"], config["model"])
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "analysis": "Model call failed.",
            "answer": "LLM request failed, please check network/key/model settings.",
            "actions": [str(exc)],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }


# ============================================================
# Streaming call (SSE, for real-time typewriter UX)
# ============================================================

async def stream_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_retries: int = 2,
    timeout: float = 30.0,
) -> AsyncGenerator[str | dict[str, Any], None]:
    """Stream LLM response with system role and retry logic.

    Args:
        system_prompt: System-level instructions (identity, task, output format).
                       Sent as role="system".
        user_prompt: Context blocks + user input. Sent as role="user".

    Yields:
        str  — answer text tokens (typewriter stream)
        dict — final structured reply (stream_interrupted=True marks interruption)
    """
    config = _get_llm_config(model_override=model)

    if not config["api_key"]:
        yield {
            "analysis": "Missing API key.",
            "answer": "LLM_API_KEY is not configured.",
            "actions": ["Set backend/.env with LLM_API_KEY and retry."],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }
        return

    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    MARKER = "---JSON---"

    for attempt in range(max_retries + 1):
        full_content = ""
        in_json = False
        json_buffer = ""
        text_output = ""
        text_pending = ""
        connection_ok = False

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    json={
                        "model": config["model"],
                        "temperature": 0.2,
                        "stream": True,
                        "max_tokens": 400,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {config['api_key']}",
                    },
                ) as response:
                    connection_ok = True

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content", "")
                            )
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        if not delta:
                            continue
                        full_content += delta

                        if in_json:
                            json_buffer += delta
                        elif MARKER in full_content:
                            parts = full_content.split(MARKER, 1)
                            clean_text = parts[0]
                            new_chars = clean_text[len(text_output):]
                            if new_chars:
                                yield new_chars
                                text_output += new_chars
                            in_json = True
                            json_buffer = parts[1] if len(parts) > 1 else ""
                        else:
                            text_pending += delta
                            safe_len = max(0, len(text_pending) - len(MARKER))
                            if safe_len > 0:
                                safe = text_pending[:safe_len]
                                yield safe
                                text_output += safe
                                text_pending = text_pending[safe_len:]

            # Stream completed normally — parse JSON
            try:
                json_str = json_buffer.strip()
                parsed = json.loads(json_str)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON is not a dict")
            except (json.JSONDecodeError, ValueError):
                try:
                    parsed = _extract_json(full_content)
                except (json.JSONDecodeError, ValueError):
                    parsed = {
                        "analysis": "",
                        "answer": full_content.split("---JSON---")[0][:200],
                        "actions": [],
                        "play_keyword": "",
                    }

            text_answer = full_content.split("---JSON---")[0].strip()
            normalized = _normalize_response(parsed, config["provider"], config["model"])
            normalized["answer"] = text_answer if text_answer else parsed.get("answer", "")
            normalized["stream_interrupted"] = False
            yield normalized
            return

        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if not connection_ok and attempt < max_retries:
                logger.warning(
                    "LLM stream connection failed (attempt %d/%d), retrying...: %s",
                    attempt + 1, max_retries + 1, exc,
                )
                continue

            yield {
                "analysis": "Model call failed.",
                "answer": (
                    "流式响应中断，请稍后重试。"
                    if connection_ok
                    else "无法连接到 LLM 服务，请检查网络和 API 配置。"
                ),
                "actions": [str(exc)],
                "play_keyword": "",
                "provider": config["provider"],
                "model": config["model"],
                "stream_interrupted": connection_ok,
                "retries_exhausted": not connection_ok and attempt >= max_retries,
            }
            return

        except (json.JSONDecodeError, ValueError) as exc:
            yield {
                "analysis": "Model call failed.",
                "answer": "LLM 返回格式异常，请重试。",
                "actions": [str(exc)],
                "play_keyword": "",
                "provider": config["provider"],
                "model": config["model"],
                "stream_interrupted": False,
            }
            return
