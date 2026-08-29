"""会话 trace 查看器 —— 人性化展示 data/conversations.jsonl 的交互记录。

用法（仓库根目录）:
    # 最近 20 条交互
    python scripts/view_conversations.py

    # 指定条数与过滤
    python scripts/view_conversations.py --tail 50 --intent music_recommend
    python scripts/view_conversations.py --session <session_id>
    python scripts/view_conversations.py --error       # 只看失败/错误记录

说明: 原始日志位于 backend/data/conversations.jsonl（JSONL，可 grep/jq）。
LLM 级调用日志位于 backend/data/llm_calls.jsonl（provider/延迟/token 统计）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data_config import get_data_dir  # noqa: E402


def _fmt_time(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description="查看会话交互 trace")
    parser.add_argument("--tail", type=int, default=20, help="最近 N 条（默认 20）")
    parser.add_argument("--session", type=str, default=None, help="按 session_id 过滤")
    parser.add_argument("--intent", type=str, default=None, help="按意图过滤 (music_play 等)")
    parser.add_argument("--error", action="store_true", help="只看错误记录")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args()

    log_path = get_data_dir() / "conversations.jsonl"
    if not log_path.exists():
        print(f"无会话日志: {log_path}（先与 DJ 对话几次再查看）")
        return 1

    records: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if args.session:
        records = [r for r in records if r.get("session_id") == args.session]
    if args.intent:
        records = [r for r in records if r.get("intent") == args.intent]
    if args.error:
        records = [r for r in records if r.get("error")]

    records = records[-args.tail:]
    if not records:
        print("无匹配记录。")
        return 1

    if args.json:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
        return 0

    for r in records:
        music = r.get("music") or {}
        music_str = (
            f" ▶ {music.get('artist')} - {music.get('name')}"
            if music.get("name") else ""
        )
        tools = r.get("tool_calls") or []
        tool_str = ""
        if tools:
            kws = [t.get("keyword", "") for t in tools if t.get("keyword")]
            tool_str = f"  [tool: {', '.join(kws)}]" if kws else f"  [tool ×{len(tools)}]"
        err = f"  [ERROR: {r.get('error')}]" if r.get("error") else ""
        print(f"{_fmt_time(r.get('ts', 0))} | {r.get('session_id', '?')[:16]:<18}"
              f"| {r.get('intent', '?'):<16} | {r.get('path', '?'):<24}"
              f"| {r.get('latency_ms', 0):>6.0f}ms")
        print(f"    Q: {r.get('user_input', '')}")
        print(f"    A: {r.get('answer', '')[:160]}{music_str}{tool_str}{err}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())