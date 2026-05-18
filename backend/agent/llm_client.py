import json
import os
from collections.abc import AsyncGenerator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx


def _get_llm_config(model_override: str | None = None) -> dict[str, str]:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    configured_model = os.getenv("LLM_MODEL", "").strip() or "deepseek-chat"
    model = model_override.strip() if isinstance(model_override, str) and model_override.strip() else configured_model
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


def _request_chat_json(
    config: dict[str, str],
    messages: list[dict[str, str]],
    temperature: float,
) -> dict[str, Any]:
    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
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

    with urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    completion = json.loads(raw)
    content = completion["choices"][0]["message"]["content"]
    return _extract_json(content)


def request_json_object(
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
    return _request_chat_json(config, messages, temperature)


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


def call_llm(prompt: str, model: str | None = None) -> dict[str, Any]:
    """Call DeepSeek (OpenAI-compatible API) and return strict JSON.

    Response schema:
    - analysis: brief reasoning summary
    - answer: final user-facing answer
    - actions: next steps list
    - play_keyword: required only when user intent is to play music
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
    ]

    try:
        parsed = _request_chat_json(config, messages, temperature=0.2)
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


# ============================================================
# Streaming support (SSE, for real-time typewriter UX + future TTS)
# ============================================================

_STREAM_SYSTEM_PROMPT = """You are Aud.IO's streaming DJ core.

Output in two parts, separated by the marker ---JSON--- on its own line:

Part 1: Your natural spoken answer to the user. Be warm, concise, DJ-like.
Write this as if you're speaking on air. Use the same language the user uses.

Part 2: A single JSON line with these keys:
- analysis: brief reasoning
- actions: list of tool calls, each a JSON object like {"tool": "search_music", "keyword": "Artist Song"}
- play_keyword: the music search phrase (Artist SongTitle format), or empty string

Example output:
Hi there! The weather is perfect for some chill jazz. Here is Miles Davis for you.
---JSON---
{"analysis": "user wants jazz", "actions": [{"tool": "search_music", "keyword": "Miles Davis So What"}], "play_keyword": "Miles Davis So What"}

IMPORTANT: The JSON part must be valid JSON on a single line after the ---JSON--- marker."""


async def stream_llm(
    prompt: str,
    model: str | None = None,
    max_retries: int = 2,
    timeout: float = 30.0,
) -> AsyncGenerator[str | dict[str, Any], None]:
    """Stream LLM response with retry logic for connection failures.

    重试策略（区分两种失败场景）:
    - 场景 A「连接建立失败」: client.stream() 本身抛异常（TCP/TLS/HTTP 握手失败，
      DNS 解析失败，连接超时等），此时尚未向 LLM 服务端发送完整请求体，
      且前端没有收到任何 token —— **安全重试**
    - 场景 B「推流中途中断」: 连接已建立，但在 aiter_lines() 迭代过程中中断。
      此时前端可能已渲染部分打字机文本，重试会导致重复/覆盖显示 —— **不重试，
      直接上报错误**

    同时顺便修复架构报告 #4.1.4 / #4.1.5 —— timeout 从硬编码改为参数。

    Yields:
        str  — answer text tokens (typewriter stream)
        dict — final structured reply (含 stream_interrupted=True 标记中断场景)
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

    full_prompt = f"{_STREAM_SYSTEM_PROMPT}\n\n{prompt}"

    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": 0.2,
        "stream": True,
        "messages": [
            {"role": "user", "content": full_prompt},
        ],
    }

    MARKER = "---JSON---"

    for attempt in range(max_retries + 1):
        # ---- 每次重试都重置的流式状态 ----
        full_content = ""
        in_json = False
        json_buffer = ""
        text_output = ""   # 已安全发送给调用方的干净文本
        text_pending = ""  # 缓冲区末尾 N 字符，用于防止 MARKER 部分泄露
        connection_ok = False  # HTTP 连接是否已建立（收到响应头）

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    endpoint,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {config['api_key']}",
                    },
                ) as response:
                    # ====== 连接已建立 ======
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
                            # 检测到 MARKER —— 清洗剩余文本，之后内容归入 JSON 缓冲
                            parts = full_content.split(MARKER, 1)
                            clean_text = parts[0]
                            new_chars = clean_text[len(text_output):]
                            if new_chars:
                                yield new_chars
                                text_output += new_chars
                            in_json = True
                            json_buffer = parts[1] if len(parts) > 1 else ""
                        else:
                            # 用滑动窗口防止 MARKER 部分泄露到前端显示
                            text_pending += delta
                            safe_len = max(0, len(text_pending) - len(MARKER))
                            if safe_len > 0:
                                safe = text_pending[:safe_len]
                                yield safe
                                text_output += safe
                                text_pending = text_pending[safe_len:]

            # ====== 流正常结束 —— 解析 JSON ======
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
            normalized["answer"] = text_answer
            normalized["stream_interrupted"] = False
            yield normalized
            return  # ← 成功，退出重试循环

        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            # ====== 场景 A: 连接未建立 → 安全重试 ======
            if not connection_ok and attempt < max_retries:
                import logging
                logging.getLogger(__name__).warning(
                    "LLM stream connection failed (attempt %d/%d), retrying...: %s",
                    attempt + 1, max_retries + 1, exc,
                )
                continue

            # ====== 场景 B: 连接已建立但中途中断 → 不重试 ======
            # 原因：前端可能已经渲染了部分打字机文本，重试会产生重复/覆盖的诡异体验
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
            # JSON 解析失败不属于网络问题，不重试
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
