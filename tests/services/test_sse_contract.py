"""SSE 序列化契约测试（RFC-019 §4.C / N20）。

被测对象：``AssistantService._sse`` —— 后端把 (event, data) 编成 SSE 线格式的唯一出口。

为什么单独成文件（而不是并入 test_assistant_service.py）：
    本文件是**跨语言契约**的 Python 侧，断言的是"线格式语义"，
    不是 assistant_service 的业务行为。与前端 test_sse_injection.mjs 成对。

设计要点（关键）：
    ``_sse`` 是 ``@staticmethod``，因此本文件可直接调用，**不需要 chromadb、
    不需要实例化 AssistantService、不需要任何网络**。属于最快能跑起来的一批
    安全回归测试。

断言策略：
    使用一个与 ``frontend/src/stores/sse-parser.js`` 语义**逐行对齐**的参考解析器，
    断言 ``parse(_sse(ev, data))`` 能**原样还原**出 ``(ev, data)``。
    这样测试不绑定具体实现（行长前缀 / JSON 转义 / 任意方案皆可），
    只绑定"线格式必须可无损往返"这一契约。

    之所以要求"无损往返"而非"JSON 可解析"：
        token 事件的 data 是**裸文本**，前端直接渲染；
        music/done 事件的 data 是 JSON 字符串，前端 JSON.parse。
        两种消费方式都要求 data 一字不差地到达前端。
"""

from __future__ import annotations

import inspect
import json
import re
import sys

import pytest

from backend.services.assistant_service import AssistantService


# ============================================================
# 参考实现：与 frontend/src/stores/sse-parser.js 语义逐行对齐
# ============================================================


def parse_sse(wire: str) -> list[dict[str, str]]:
    """按 SSE 线格式解析，语义与前端 SSEParser.feed() 保持一致。

    对应 JS：
        idx = buffer.indexOf('\\n\\n')      → 以空行为帧边界
        line.startsWith('event: ')          → 设置 eventType
        line.startsWith('data: ')           → 追加 dataLines
        其余行                              → **丢弃**（这正是丢字 bug 的来源）
        if (eventType) push(...)            → 空 eventType 的帧被静默丢弃
    """
    events: list[dict[str, str]] = []
    buf = wire

    while True:
        idx = buf.find("\n\n")
        if idx == -1:
            break
        block, buf = buf[:idx], buf[idx + 2 :]

        event_type = ""
        data_lines: list[str] = []

        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
            # else: 裸行 —— 前端会直接丢弃

        if event_type:
            events.append({"event": event_type, "data": "\n".join(data_lines)})

    return events


def sse(event: str, data: str) -> str:
    """调用被测函数（staticmethod，无需实例）。"""
    return AssistantService._sse(event, data)


# ============================================================
# 注入载荷（与前端 test_sse_injection.mjs 保持同一组）
# ============================================================

# 载荷 ①：单一换行 —— 旧实现下第二行起全部丢失
PAYLOAD_SINGLE_NL = "第一行\n第二行\n第三行"

# 载荷 ②：双换行 —— 旧实现下制造帧边界，后续内容成为孤立块被丢弃
PAYLOAD_DOUBLE_NL = "第一段\n\n第二段"

# 载荷 ③：事件类型劫持（旧实现下 event 被改成 music）
PAYLOAD_HIJACK = '安全文本\nevent: music\ndata: {"mp3_url":"http://evil/x.mp3"}'

# 载荷 ④：**可达播放**的劫持 —— JSON 在前，data 为干净合法 JSON
#          （旧实现下前端 JSON.parse 成功并触发 player.playTrack）
PAYLOAD_HIJACK_PLAYABLE = '{"mp3_url":"http://evil/x.mp3"}\nevent: music'

# 载荷 ⑤：帧伪造 —— 内嵌 \n\n 凭空造出第二个完整事件帧
PAYLOAD_FRAME_FORGERY = 'a\n\nevent: music\ndata: {"mp3_url":"http://evil/y.mp3"}'

INJECTION_PAYLOADS = [
    pytest.param(PAYLOAD_SINGLE_NL, id="single-newline"),
    pytest.param(PAYLOAD_DOUBLE_NL, id="double-newline"),
    pytest.param(PAYLOAD_HIJACK, id="event-hijack"),
    pytest.param(PAYLOAD_HIJACK_PLAYABLE, id="event-hijack-playable"),
    pytest.param(PAYLOAD_FRAME_FORGERY, id="frame-forgery"),
]


# ============================================================
# 一、基础契约
# ============================================================


class TestSseBasicContract:
    def test_single_line_roundtrip(self) -> None:
        """普通单行 data 必须无损往返。"""
        events = parse_sse(sse("token", "今天天气不错"))

        assert len(events) == 1
        assert events[0]["event"] == "token"
        assert events[0]["data"] == "今天天气不错"

    def test_event_name_preserved(self) -> None:
        """事件名不得被 data 内容影响。"""
        events = parse_sse(sse("music", '{"song_id":"1"}'))

        assert len(events) == 1
        assert events[0]["event"] == "music"
        assert events[0]["data"] == '{"song_id":"1"}'

        # 前端靠 JSON.parse 消费 music；必须仍是合法 JSON
        assert json.loads(events[0]["data"]) == {"song_id": "1"}

    def test_empty_data(self) -> None:
        """空 data 仍应产出恰好一个事件（不得变成 0 个或 2 个）。"""
        events = parse_sse(sse("text", ""))

        assert len(events) == 1
        assert events[0]["event"] == "text"
        assert events[0]["data"] == ""

    def test_unicode_and_emoji_preserved(self) -> None:
        """非 ASCII 与 emoji 不得被转换或截断。"""
        payload = "🎵 周杰伦 — 晴天 ☀️ 100% 完整"
        events = parse_sse(sse("token", payload))

        assert len(events) == 1
        assert events[0]["data"] == payload


# ============================================================
# 二、换行安全（N20 核心：丢字）
# ============================================================


class TestSseNewlineSafety:
    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_payload_yields_exactly_one_event(self, payload: str) -> None:
        """任意注入载荷都必须只产出 1 个事件，不得凭空多出帧。"""
        events = parse_sse(sse("token", payload))

        assert len(events) == 1, (
            f"载荷产生了 {len(events)} 个事件，期望 1 个：{events!r}"
        )

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_payload_event_type_stays_token(self, payload: str) -> None:
        """任意注入载荷都不得改写事件类型（尤其不得变成 music）。"""
        events = parse_sse(sse("token", payload))

        assert events[0]["event"] == "token", (
            f"事件类型被改写为 {events[0]['event']!r} —— 这是帧注入"
        )

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_payload_data_roundtrips_exactly(self, payload: str) -> None:
        """data 必须一字不差地到达前端 —— 多行内容不得丢行。"""
        events = parse_sse(sse("token", payload))

        assert events[0]["data"] == payload, (
            f"data 未能无损往返\n  期望: {payload!r}\n  实际: {events[0]['data']!r}"
        )

    def test_wire_contains_exactly_one_event_field(self) -> None:
        """线格式中 ``event: `` **字段行**只能出现 1 次。

        注意断言的量：这里数的是**以 ``event: `` 开头的行**，不是裸子串。

        原因：``data: `` 前缀是"降级"而非"删除"。载荷里的 ``event: music``
        会变成 ``data: event: music`` —— 子串仍然在，但已落在 data 行内，
        解析器只按行首前缀判定，不会把它当成控制字段。
        任何保留 data 原文无损的方案（这是 token 事件渲染的前提）都必然让
        裸子串计数等于 2，因此裸子串计数不是可满足的契约。
        """
        wire = sse("token", PAYLOAD_HIJACK)

        body = wire[:-2] if wire.endswith("\n\n") else wire
        event_fields = [
            line for line in body.split("\n") if line.startswith("event: ")
        ]

        assert len(event_fields) == 1, (
            f"出现 {len(event_fields)} 个 'event: ' 字段行，注入载荷逃逸成了控制帧："
            f"{event_fields!r}"
        )
        assert event_fields[0] == "event: token"

    def test_wire_body_has_no_bare_double_newline(self) -> None:
        """帧边界（``\\n\\n``）只能出现在结尾，载荷内部的不能逃逸。"""
        wire = sse("token", PAYLOAD_FRAME_FORGERY)

        # 去掉结尾的帧终止符后，正文里不应再有空行
        body = wire[:-2] if wire.endswith("\n\n") else wire
        assert "\n\n" not in body, (
            f"载荷内含的 \\n\\n 逃逸为帧边界，正文：{body!r}"
        )

    def test_every_body_line_is_a_field_line(self) -> None:
        """正文每一行都必须是 ``event:`` / ``data:`` 字段行，不得出现裸行。"""
        wire = sse("token", PAYLOAD_SINGLE_NL)

        body = wire[:-2] if wire.endswith("\n\n") else wire
        bare = [
            line
            for line in body.split("\n")
            if line and not line.startswith(("event: ", "data: "))
        ]

        assert bare == [], f"出现裸行（前端会静默丢弃）：{bare!r}"


# ============================================================
# 三、跨事件一致性
# ============================================================


class TestSseAcrossEventTypes:
    @pytest.mark.parametrize(
        "event", ["token", "text", "music", "done", "status", "error", "speech"]
    )
    def test_all_event_types_roundtrip(self, event: str) -> None:
        """全部 7 种事件类型都必须满足同一份线格式契约。"""
        payload = '第一行\n第二行\n{"k":"v"}'
        events = parse_sse(sse(event, payload))

        assert len(events) == 1
        assert events[0]["event"] == event
        assert events[0]["data"] == payload

    def test_json_payload_stays_valid_after_encoding(self) -> None:
        """music/done 的 data 经编码后仍必须是可 JSON.parse 的原文。"""
        music = {"song_id": "186016", "name": "晴天", "artist": "周杰伦"}
        wire_payload = json.dumps(music, ensure_ascii=False)

        events = parse_sse(sse("music", wire_payload))

        assert json.loads(events[0]["data"]) == music


# ============================================================
# 静态防腐：钉住实现形态，拦「docstring 已更新、函数体未更新」的半成品
# ============================================================


class TestSseImplementationShape:
    """运行时用例拦不住的形态：docstring 描述新行为，函数体仍是旧裸内插。

    这类半成品在「实现半改 + 测试也半改」时会互相掩盖，所以必须用源码级
    断言钉住 —— 2026-10-18 T3 实施过程中真实出现过这种中间态。
    """

    def test_body_uses_line_prefixed_construction(self) -> None:
        """函数体必须真的逐行加 `data: ` 前缀，而非只更新了 docstring。"""
        src = inspect.getsource(AssistantService._sse)

        assert "data: {line}" in src, (
            "_sse 必须逐行构造 `data: ` 前缀；缺失说明实现未落地或被改回旧写法"
        )

    def test_body_does_not_interpolate_data_directly(self) -> None:
        """禁止回到裸内插 —— 那正是 N20 三种协议缺陷的根因。"""
        src = inspect.getsource(AssistantService._sse)
        # 剥掉 docstring 只看函数体，避免 docstring 里的示例代码误触断言
        body = src.split('"""', 2)[-1]

        assert 'f"event: {event}\\ndata: {data}\\n\\n"' not in body, (
            "_sse 不得把 data 直接内插进帧字符串（N20 裸内插形态）"
        )

    def test_done_payload_is_json_dumped(self) -> None:
        """所有 `_sse("done", ...)` 的第二个实参必须以 `json.dumps(` 开头。

        `routes_agent.py` 用 `split("data: ", 1)[1]` 取 done 载荷，隐含假设
        「一帧只有一个 data 行」。它今天安全纯粹因为 done 恒为
        `json.dumps` 单行 —— 这是巧合，不是契约。

        若有人往 done 里塞多行字符串：`split("data: ", 1)[1]` 只拿到第一行
        → `json.loads` 失败 → 被 `except` 吞掉 → `full_reply` 置空 →
        **画像更新静默跳过（第二个 N22 复活）**。

        选静态扫描而非运行时断言：运行时只覆盖构造出的那一份载荷，
        无法阻止新增第二个 `_sse("done", ...)` 调用点用错编码。
        """
        module_src = inspect.getsource(sys.modules[AssistantService.__module__])

        done_calls = re.findall(r'_sse\(\s*"done"\s*,\s*([^)]+)', module_src)

        assert done_calls, "未找到任何 _sse(\"done\", ...) 调用点"
        for arg in done_calls:
            arg = arg.strip()
            assert arg.startswith("json.dumps("), (
                f"done 载荷必须以 json.dumps( 开头，实际为: {arg[:60]!r}"
            )
