"""LLM client — unified httpx async backend for both streaming and non-streaming calls.

v0.4 (RFC-007): System prompt sent as role="system" (not concatenated into user message).
Callers pass system_prompt and user_prompt separately.
P2-1: 每次调用写入结构化日志（data/llm_calls.jsonl），供 /ready 指标与排查使用。
"""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


def _log_llm_call(
    provider: str, model: str, endpoint: str,
    latency_ms: float, ok: bool, stream: bool,
    input_chars: int = 0, output_chars: int = 0,
    note: str = "",
) -> None:
    """结构化记录一次 LLM 调用（JSONL 追加，失败只告警——日志绝不影响主流程）。"""
    try:
        from backend.data_config import get_data_dir

        log_dir = get_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "latency_ms": round(latency_ms, 1),
            "ok": ok,
            "stream": stream,
            "input_chars": input_chars,
            "output_chars": output_chars,
            "note": note,
        }
        with (log_dir / "llm_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("LLM 调用日志写入失败（不影响主流程）: %s", exc)


def _get_llm_config(model_override: str | None = None) -> dict[str, str]:
    provider = settings.llm_provider.strip().lower()
    configured_model = settings.llm_model.strip()
    model = model_override.strip() if isinstance(model_override, str) and model_override.strip() else configured_model
    base_url = settings.llm_base_url.strip()

    api_key = settings.llm_api_key.strip()
    if not api_key:
        # Provider-specific fallback keys — only for OpenAI-compatible APIs.
        # Anthropic uses the Messages API (not /chat/completions) and is not
        # supported by this client.  Use LLM_API_KEY for all providers.
        if provider == "openai":
            api_key = settings.openai_api_key.strip()
        elif provider == "deepseek":
            api_key = settings.deepseek_api_key.strip()

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def _provider_extra_body(provider: str) -> dict[str, Any]:
    """Provider-specific request body extensions.

    deepseek-v4-flash 是推理模型：默认会先流数百 token 的 reasoning_content，
    max_tokens 预算被思考吃光后 content 为空（流式回答/JSON 解析全部失败）。
    显式禁用 thinking，让输出预算全部留给真实回答。
    其他 OpenAI 兼容 provider 会拒绝未知参数，因此仅对 deepseek 生效；
    LLM_DISABLE_THINKING=false 可关闭（兼容不支持该参数的老 deepseek 模型）。
    """
    if provider == "deepseek" and settings.llm_disable_thinking:
        return {"thinking": {"type": "disabled"}}
    return {}


def _tool_calls_to_actions(tool_calls: list[Any]) -> list[dict[str, Any]]:
    """Convert OpenAI tool_calls → Aud.IO action dicts {"tool": name, ...args}.

    Arguments arrive as JSON string fragments (may be complete or streamed-joined);
    parse them and merge into the action dict. Unparseable arguments are kept raw
    under the "arguments" key rather than dropping the call.
    """
    actions: list[dict[str, Any]] = []
    for tc in tool_calls or []:
        fn = tc.get("function") or {}
        name = str(fn.get("name", "")).strip()
        if not name:
            continue
        action: dict[str, Any] = {"tool": name}
        args = str(fn.get("arguments") or "").strip()
        if args:
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict):
                    action.update(parsed)
                else:
                    action["value"] = parsed
            except json.JSONDecodeError:
                action["arguments"] = args
        actions.append(action)
    return actions


async def _request_chat_plain(
    config: dict[str, str],
    messages: list[dict[str, str]],
    temperature: float,
    timeout: float = 30.0,
) -> str:
    """Non-streaming chat completion WITHOUT response_format=json_object.

    供 call_llm 的自然语言路径使用——NON_STREAMING_SYSTEM 重构后要求纯文本回答，
    若仍强制 json_object，模型会把整段回答包进 JSON 键（{"message": "..."}），
    下游按 answer 键解析得到空串（回归: step-4 重构）。
    JSON 消费者（memory observer / reflection）走 _request_chat_json，不受影响。
    """
    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": temperature,
        "max_tokens": 600,
        "messages": messages,
        **_provider_extra_body(config["provider"]),
    }

    start = time.monotonic()
    try:
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
    except Exception as exc:
        _log_llm_call(
            config["provider"], config["model"], endpoint,
            (time.monotonic() - start) * 1000, False, False,
            input_chars=sum(len(m.get("content", "")) for m in messages),
            note=f"{type(exc).__name__}",
        )
        raise

    content = completion["choices"][0]["message"].get("content") or ""
    _log_llm_call(
        config["provider"], config["model"], endpoint,
        (time.monotonic() - start) * 1000, True, False,
        input_chars=sum(len(m.get("content", "")) for m in messages),
        output_chars=len(str(content)),
    )
    return str(content)


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
        **_provider_extra_body(config["provider"]),
    }

    start = time.monotonic()
    try:
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
    except Exception as exc:
        _log_llm_call(
            config["provider"], config["model"], endpoint,
            (time.monotonic() - start) * 1000, False, False,
            input_chars=sum(len(m.get("content", "")) for m in messages),
            note=f"{type(exc).__name__}",
        )
        raise
    _log_llm_call(
        config["provider"], config["model"], endpoint,
        (time.monotonic() - start) * 1000, True, False,
        input_chars=sum(len(m.get("content", "")) for m in messages),
        output_chars=len(str(completion.get("choices", [{}])[0].get("message", {}).get("content", ""))),
    )
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
    # RFC: function calling — actions 现在是 dict 列表 ({"tool": name, ...args})，
    # 不再强制转字符串
    if not isinstance(actions, list):
        actions = [str(actions)] if actions not in (None, "") else []
    else:
        actions = [item for item in actions if isinstance(item, (dict, str))]
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
    tools: list[dict[str, Any]] | None = None,
    force_tools: bool = False,
) -> dict[str, Any]:
    """Call OpenAI-compatible API with system role and return structured JSON.

    Args:
        system_prompt: System-level instructions (identity, task, output format).
                       Sent as role="system".
        user_prompt: Context blocks + user input. Sent as role="user".
        tools: Optional OpenAI function schemas (native function calling).
               When provided, the model may answer with tool_calls instead of
               JSON — tool calls are normalized into the "actions" key.
        force_tools: 强制至少调用一次工具（tool_choice="required"）。
                     音乐推荐类意图使用——防止模型只写文案不调工具导致无法播放。

    Response schema:
    - analysis: brief reasoning summary (may be empty)
    - answer: final user-facing answer
    - actions: tool calls as [{"tool": name, ...args}] (RFC: function calling)
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
        if tools:
            content, tool_calls = await _request_chat_with_tools(
                config, messages, temperature=0.2, tools=tools,
                force_tools=force_tools,
            )
            payload = {
                "analysis": "",
                "answer": content or "",
                "actions": _tool_calls_to_actions(tool_calls),
                "play_keyword": "",
            }
        else:
            # 自然语言路径——绝不用 json_object（见 _request_chat_plain 注释）
            content = await _request_chat_plain(config, messages, temperature=0.2)
            payload = {
                "analysis": "",
                "answer": content or "",
                "actions": [],
                "play_keyword": "",
            }
        return _normalize_response(payload, config["provider"], config["model"])
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "analysis": "Model call failed.",
            "answer": "LLM request failed, please check network/key/model settings.",
            "actions": [str(exc)],
            "play_keyword": "",
            "provider": config["provider"],
            "model": config["model"],
        }


async def _request_chat_with_tools(
    config: dict[str, str],
    messages: list[dict[str, str]],
    temperature: float,
    tools: list[dict[str, Any]],
    timeout: float = 30.0,
    force_tools: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Non-streaming chat completion with native function calling.

    Returns (content, tool_calls). The model may return either (or both).
    """
    endpoint = f"{config['base_url'].rstrip('/')}/chat/completions"
    body = {
        "model": config["model"],
        "temperature": temperature,
        "max_tokens": 600,
        "messages": messages,
        "tools": tools,
        "tool_choice": "required" if force_tools else "auto",
        **_provider_extra_body(config["provider"]),
    }

    start = time.monotonic()
    try:
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
    except Exception as exc:
        _log_llm_call(
            config["provider"], config["model"], endpoint,
            (time.monotonic() - start) * 1000, False, False,
            input_chars=sum(len(m.get("content", "")) for m in messages),
            note=f"{type(exc).__name__}",
        )
        raise

    message = completion["choices"][0]["message"]
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    _log_llm_call(
        config["provider"], config["model"], endpoint,
        (time.monotonic() - start) * 1000, True, False,
        input_chars=sum(len(m.get("content", "")) for m in messages),
        output_chars=len(str(content)),
        note=f"tool_calls={len(tool_calls)}",
    )
    return str(content), list(tool_calls)


# ============================================================
# Streaming call (SSE, for real-time typewriter UX)
# ============================================================

async def stream_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_retries: int = 2,
    timeout: float = 30.0,
    tools: list[dict[str, Any]] | None = None,
    force_tools: bool = False,
) -> AsyncGenerator[str | dict[str, Any], None]:
    """Stream LLM response with system role and retry logic.

    Args:
        system_prompt: System-level instructions (identity, task, output format).
                       Sent as role="system".
        user_prompt: Context blocks + user input. Sent as role="user".
        tools: Optional OpenAI function schemas (native function calling).
               Tool calls are accumulated from deltas and normalized into
               the final dict's "actions" key.
        force_tools: 强制至少调用一次工具（tool_choice="required"）。

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
    body: dict[str, Any] = {
        "model": config["model"],
        "temperature": 0.2,
        "stream": True,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        **_provider_extra_body(config["provider"]),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "required" if force_tools else "auto"

    for attempt in range(max_retries + 1):
        full_content = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        connection_ok = False
        start = time.monotonic()

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
                    connection_ok = True

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choice = chunk.get("choices", [{}])[0] if chunk.get("choices") else {}
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                        if content:
                            full_content += content
                            yield content

                        # RFC: function calling — accumulate tool_calls deltas
                        # 保持 OpenAI 原始嵌套结构 {"function": {name, arguments}}，
                        # 与 _tool_calls_to_actions 的解析格式一致
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            entry = tool_calls_acc.setdefault(
                                idx, {"id": "", "function": {"name": "", "arguments": ""}}
                            )
                            if tc.get("id"):
                                entry["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                entry["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                entry["function"]["arguments"] += fn["arguments"]

            # Stream completed normally — normalize tool calls
            raw_tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            normalized = _normalize_response(
                {"analysis": "", "answer": full_content, "actions": []},
                config["provider"], config["model"],
            )
            normalized["answer"] = full_content
            normalized["actions"] = _tool_calls_to_actions(raw_tool_calls)
            normalized["stream_interrupted"] = False
            _log_llm_call(
                config["provider"], config["model"], endpoint,
                (time.monotonic() - start) * 1000, True, True,
                input_chars=len(system_prompt) + len(user_prompt),
                output_chars=len(full_content),
                note=f"tool_calls={len(raw_tool_calls)}",
            )
            yield normalized
            return

        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if not connection_ok and attempt < max_retries:
                logger.warning(
                    "LLM stream connection failed (attempt %d/%d), retrying...: %s",
                    attempt + 1, max_retries + 1, exc,
                )
                continue

            _log_llm_call(
                config["provider"], config["model"], endpoint,
                (time.monotonic() - start) * 1000, False, True,
                input_chars=len(system_prompt) + len(user_prompt),
                note=f"{type(exc).__name__}",
            )
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
