"""LLM 结构化日志测试（P2-1）—— JSONL 写入、指标统计口径。"""

import json

from backend.agent import llm_client
from backend.config import settings


class TestLlmCallLog:
    def test_log_writes_jsonl(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        llm_client._log_llm_call(
            provider="deepseek", model="deepseek-v4-flash",
            endpoint="https://api.deepseek.com/chat/completions",
            latency_ms=123.4, ok=True, stream=True,
            input_chars=100, output_chars=50, note="tool_calls=1",
        )
        log_file = tmp_path / "llm_calls.jsonl"
        assert log_file.exists()
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["provider"] == "deepseek"
        assert record["model"] == "deepseek-v4-flash"
        assert record["ok"] is True
        assert record["stream"] is True
        assert record["latency_ms"] == 123.4
        assert record["note"] == "tool_calls=1"

    def test_log_appends_multiple(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        llm_client._log_llm_call("d", "m", "e", 10, True, False)
        llm_client._log_llm_call("d", "m", "e", 20, False, False, note="TimeoutException")
        lines = (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["ok"] is False

    def test_log_never_raises_on_bad_dir(self, monkeypatch) -> None:
        # 路径不可写（指向文件）——日志必须静默失败
        import tempfile

        bad_path = tempfile.mktemp()  # 不存在的父路径下文件
        monkeypatch.setattr(settings, "aud_io_data_dir", str(bad_path))
        llm_client._log_llm_call("d", "m", "e", 1, True, False)  # 不应抛异常