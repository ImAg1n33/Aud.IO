import { describe, it, beforeEach } from 'node:test';
import { strict as assert } from 'node:assert';
import { sendFeedback } from '../../frontend/src/stores/feedback.js';

// localStorage 与 fetch 的轻量 stub（Node 无 DOM）
const storage = new Map();
globalThis.localStorage = {
  getItem: (k) => storage.get(k) ?? null,
  setItem: (k, v) => storage.set(k, String(v)),
};

function stubFetch() {
  let calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true };
  };
  return calls;
}

describe('sendFeedback', () => {
  beforeEach(() => {
    storage.clear();
    storage.set('aud_io_session', 'session-abc');
  });

  it('sends feedback with song_id and session_id', async () => {
    const calls = stubFetch();
    const ok = await sendFeedback('song_finished', { song_id: '123', name: 'X', artist: 'Y' }, 180);

    assert.equal(ok, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/v1/agent/feedback');
    const body = JSON.parse(calls[0].options.body);
    assert.deepEqual(body, {
      event: 'song_finished',
      song_id: '123',
      listen_seconds: 180,
      session_id: 'session-abc',
    });
  });

  it('uses keepalive for reliable delivery on page close', async () => {
    const calls = stubFetch();
    await sendFeedback('song_started', { song_id: '123' });
    assert.equal(calls[0].options.keepalive, true);
  });

  it('returns false when track has no song_id', async () => {
    const calls = stubFetch();
    const ok = await sendFeedback('song_finished', { name: 'X' });
    assert.equal(ok, false);
    assert.equal(calls.length, 0);
  });

  it('returns false without event', async () => {
    const calls = stubFetch();
    const ok = await sendFeedback(null, { song_id: '123' });
    assert.equal(ok, false);
    assert.equal(calls.length, 0);
  });

  it('never throws on network failure (fire-and-forget)', async () => {
    globalThis.fetch = async () => {
      throw new Error('network down');
    };
    const ok = await sendFeedback('song_skipped', { song_id: '123' }, 10);
    assert.equal(ok, false);
  });
});