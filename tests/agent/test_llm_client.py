"""LLM 客户端配置测试 —— 推理模型 thinking 禁用 + function calling 重构。

注意：模块读取 settings 单例（P2-2 配置集中），测试用
monkeypatch.setattr(settings, ...) 而非 setenv。
"""

from backend.agent.llm_client import (
    _get_llm_config,
    _normalize_response,
    _provider_extra_body,
    _tool_calls_to_actions,
)
from backend.config import settings


class TestProviderExtraBody:
    def test_deepseek_disables_thinking_by_default(self) -> None:
        assert _provider_extra_body("deepseek") == {"thinking": {"type": "disabled"}}

    def test_deepseek_thinking_disable_off(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "llm_disable_thinking", False)
        assert _provider_extra_body("deepseek") == {}

    def test_openai_gets_no_extra_body(self) -> None:
        assert _provider_extra_body("openai") == {}

    def test_unknown_provider_gets_no_extra_body(self) -> None:
        assert _provider_extra_body("custom") == {}


class TestLlMConfig:
    def test_config_includes_provider(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "llm_provider", "deepseek")
        monkeypatch.setattr(settings, "llm_model", "deepseek-v4-flash")
        config = _get_llm_config()
        assert config["provider"] == "deepseek"
        assert config["model"] == "deepseek-v4-flash"

    def test_model_override_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "llm_model", "base-model")
        config = _get_llm_config(model_override="override-model")
        assert config["model"] == "override-model"


class TestToolCallsToActions:
    """RFC: function calling —— tool_calls 归一化为 actions。"""

    def test_complete_tool_call(self) -> None:
        calls = [{
            "id": "call_1",
            "function": {"name": "search_music", "arguments": '{"keyword": "Miles Davis So What"}'},
        }]
        assert _tool_calls_to_actions(calls) == [
            {"tool": "search_music", "keyword": "Miles Davis So What"},
        ]

    def test_streamed_fragments_already_joined(self) -> None:
        """流式场景下 arguments 分片在 llm_client 已拼接为完整 JSON。"""
        calls = [{
            "function": {"name": "search_music", "arguments": '{"keyword": "Miles Davis"}'},
        }]
        assert _tool_calls_to_actions(calls) == [
            {"tool": "search_music", "keyword": "Miles Davis"},
        ]

    def test_invalid_arguments_kept_raw(self) -> None:
        calls = [{"function": {"name": "search_music", "arguments": "{broken json"}}]
        assert _tool_calls_to_actions(calls) == [
            {"tool": "search_music", "arguments": "{broken json"},
        ]

    def test_missing_name_skipped(self) -> None:
        assert _tool_calls_to_actions([{"function": {"arguments": "{}"}}]) == []
        assert _tool_calls_to_actions([{}]) == []

    def test_empty_inputs(self) -> None:
        assert _tool_calls_to_actions([]) == []
        assert _tool_calls_to_actions(None) == []

    def test_multiple_calls_preserve_order(self) -> None:
        calls = [
            {"function": {"name": "search_music", "arguments": '{"keyword": "A"}'}},
            {"function": {"name": "get_music_url", "arguments": '{"song_id": "1"}'}},
        ]
        actions = _tool_calls_to_actions(calls)
        assert actions[0]["tool"] == "search_music"
        assert actions[1]["tool"] == "get_music_url"
        assert actions[1]["song_id"] == "1"

    def test_stream_accumulated_shape_converts(self) -> None:
        """stream_llm 流式累积的嵌套结构（id + function）必须能被解析。

        回归：曾因累积为扁平 {name, arguments} 导致 actions 静默为空——
        模型实际调用了 search_music，但音乐永远不播。
        """
        acc = [{
            "id": "call_1",
            "function": {"name": "search_music", "arguments": '{"keyword": "落日飞车 My Jinji"}'},
        }]
        assert _tool_calls_to_actions(acc) == [
            {"tool": "search_music", "keyword": "落日飞车 My Jinji"},
        ]


class TestNormalizeResponseActions:
    """actions 从字符串列表升级为 dict 列表后，归一化必须保留 dict。"""

    def test_preserves_dict_actions(self) -> None:
        result = _normalize_response(
            {"analysis": "", "answer": "ok", "actions": [{"tool": "search_music", "keyword": "X"}]},
            "deepseek", "test-model",
        )
        assert result["actions"] == [{"tool": "search_music", "keyword": "X"}]

    def test_empty_actions_stay_empty(self) -> None:
        result = _normalize_response(
            {"analysis": "", "answer": "hi", "actions": []}, "deepseek", "m",
        )
        assert result["actions"] == []