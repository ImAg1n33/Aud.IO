/**
 * SSE 防劫持跨语言契约测试（RFC-019 §4.C / N20）—— Node 侧。
 *
 * 与 tests/services/test_sse_contract.py 成对：
 *   Python 侧保证 `AssistantService._sse()` 产出的线格式满足契约；
 *   Node 侧保证**生产用的同一个 SSEParser** 在该线格式下不会被载荷劫持。
 *
 * 为什么单独成文件：
 *   这是跨语言**契约测试**，不是解析器的单元测试。并入 test_sse_parser.mjs
 *   会把"解析器正确性"和"安全契约"混在一起，职责不清且难以定位。
 *
 * 运行：
 *   node --test tests/frontend/*.mjs
 */

import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';

import { SSEParser } from '../../frontend/src/stores/sse-parser.js';

/**
 * 目标线格式编码器（RFC-019 §4.C 方案甲：逐行加 `data: ` 前缀）。
 *
 * 注意：这里实现的是**修复后的目标格式**，不是当前后端实现。
 * 后端 `_sse()` 是否真的产出等价字节，由 Python 侧
 * tests/services/test_sse_contract.py 负责验证。
 * 本文件只回答一个问题：**前端解析器在这种格式下安全吗？**
 */
function encodeSse(event, data) {
  const body = String(data)
    .split('\n')
    .map((line) => `data: ${line}`)
    .join('\n');
  return `event: ${event}\n${body}\n\n`;
}

/**
 * 修复前的旧实现（裸 f-string）—— 仅用于**证明载荷是真攻击**。
 *
 * 保留它是有意的：如果某天有人把 `_sse` 改回裸拼接，
 * 下面 "documents the bug" 那组测试会说明后果。
 */
function encodeSseLegacy(event, data) {
  return `event: ${event}\ndata: ${data}\n\n`;
}

// ── 攻击载荷（与 Python 侧 INJECTION_PAYLOADS 保持同一组）──────────────

const PAYLOADS = [
  {
    id: 'single-newline',
    label: '单一换行 —— 旧实现下第二行起全部丢失',
    data: '第一行\n第二行\n第三行',
  },
  {
    id: 'double-newline',
    label: '双换行 —— 旧实现下制造帧边界',
    data: '第一段\n\n第二段',
  },
  {
    id: 'event-hijack',
    label: '事件类型劫持 —— event 被改写为 music',
    data: '安全文本\nevent: music\ndata: {"mp3_url":"http://evil/x.mp3"}',
  },
  {
    id: 'event-hijack-playable',
    label: '⚠️ 可达播放的劫持 —— JSON 在前，前端能 JSON.parse 成功',
    data: '{"mp3_url":"http://evil/x.mp3"}\nevent: music',
  },
  {
    id: 'frame-forgery',
    label: '帧伪造 —— 内嵌 \\n\\n 凭空造出第二个合法事件帧',
    data: 'a\n\nevent: music\ndata: {"mp3_url":"http://evil/y.mp3"}',
  },
];

// ── 一、目标格式下的安全性 ───────────────────────────────────────────

describe('SSE 防劫持（目标格式）', () => {
  for (const { id, label, data } of PAYLOADS) {
    it(`payload[${id}] 只产出 1 个事件 —— ${label}`, () => {
      const parser = new SSEParser();
      const events = parser.feed(encodeSse('token', data));

      assert.equal(
        events.length,
        1,
        `载荷产生了 ${events.length} 个事件，期望 1 个：${JSON.stringify(events)}`,
      );
    });

    it(`payload[${id}] 事件类型恒为 token`, () => {
      const parser = new SSEParser();
      const events = parser.feed(encodeSse('token', data));

      assert.equal(
        events[0].event,
        'token',
        `事件类型被改写为 "${events[0].event}" —— 这是帧注入`,
      );
    });

    it(`payload[${id}] data 无损往返`, () => {
      const parser = new SSEParser();
      const events = parser.feed(encodeSse('token', data));

      assert.equal(
        events[0].data,
        data,
        `data 未能无损往返\n  期望: ${JSON.stringify(data)}\n  实际: ${JSON.stringify(events[0].data)}`,
      );
    });
  }

  it('music 载荷经编码后仍可 JSON.parse（前端播放路径依赖此点）', () => {
    const music = { song_id: '186016', name: '晴天', artist: '周杰伦' };
    const parser = new SSEParser();
    const events = parser.feed(encodeSse('music', JSON.stringify(music)));

    assert.equal(events.length, 1);
    assert.equal(events[0].event, 'music');
    assert.deepEqual(JSON.parse(events[0].data), music);
  });

  it('分块喂入（模拟网络切包）不改变结果 —— 边界不得被切包影响', () => {
    const data = PAYLOADS.find((p) => p.id === 'frame-forgery').data;
    const wire = encodeSse('token', data);

    const parser = new SSEParser();
    const collected = [];
    // 逐字符切包，最恶劣的粘包/断包场景
    for (const ch of wire) {
      collected.push(...parser.feed(ch));
    }

    assert.equal(collected.length, 1);
    assert.equal(collected[0].event, 'token');
    assert.equal(collected[0].data, data);
  });
});

// ── 二、旧实现的危害（characterization：证明载荷是真攻击，不是空转）──
//
// 这组测试断言的是**旧编码器**的行为，与后端当前实现无关。
// 它的价值：如果有人质疑"这些载荷根本没用"，这里给出了可执行的反证。

describe('旧实现（裸拼接）的漏洞表征 —— 证明载荷是真攻击', () => {
  it('单换行会丢字（不是只丢 \\n\\n）', () => {
    const parser = new SSEParser();
    const payload = '第一行\n第二行\n第三行';
    const events = parser.feed(encodeSseLegacy('token', payload));

    assert.equal(events.length, 1);
    assert.equal(events[0].data, '第一行', '旧实现应丢失第二行起的内容');
    assert.notEqual(events[0].data, payload);
  });

  it('载荷可把事件类型改写为 music', () => {
    const parser = new SSEParser();
    const events = parser.feed(encodeSseLegacy('token', PAYLOADS[2].data));

    assert.equal(events[0].event, 'music', '旧实现下事件类型应被劫持');
  });

  it('⭐ 可达播放：劫持后的 data 是干净合法 JSON（前端会 player.playTrack）', () => {
    const parser = new SSEParser();
    const events = parser.feed(encodeSseLegacy('token', PAYLOADS[3].data));

    assert.equal(events.length, 1);
    assert.equal(events[0].event, 'music');

    // 关键：能 JSON.parse 成功，且带 mp3_url —— chat.js 会据此调用 playTrack
    const parsed = JSON.parse(events[0].data);
    assert.equal(parsed.mp3_url, 'http://evil/x.mp3');
  });

  it('载荷可凭空造出第二个完整事件帧', () => {
    const parser = new SSEParser();
    const events = parser.feed(encodeSseLegacy('token', PAYLOADS[4].data));

    assert.equal(events.length, 2, '旧实现下应被伪造出 2 个事件');
    assert.equal(events[0].event, 'token');
    assert.equal(events[1].event, 'music');
    assert.equal(JSON.parse(events[1].data).mp3_url, 'http://evil/y.mp3');
  });
});
