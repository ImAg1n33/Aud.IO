import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  applyEvent,
  createDJMessage,
  createUserMessage,
} from '../../frontend/src/stores/chat.js';

function newConversation(userText) {
  return [createUserMessage(userText), createDJMessage()];
}

describe('chat message reducer', () => {
  it('creates user/dj messages with expected shape', () => {
    const u = createUserMessage('来首轻松的');
    assert.equal(u.role, 'user');
    assert.equal(u.text, '来首轻松的');
    assert.equal(u.state, 'done');

    const d = createDJMessage();
    assert.equal(d.role, 'dj');
    assert.equal(d.state, 'streaming');
    assert.equal(d.song, null);
  });

  it('text event replaces dj message text', () => {
    let msgs = newConversation('你好');
    msgs = applyEvent(msgs, 'text', '晚上好，想听点什么？');
    assert.equal(msgs[1].text, '晚上好，想听点什么？');
  });

  it('music event attaches parsed song to dj message', () => {
    let msgs = newConversation('来首轻松的');
    msgs = applyEvent(msgs, 'music', JSON.stringify({
      song_id: '123', name: 'So What', artist: 'Miles Davis', mp3_url: 'http://x.mp3',
    }));
    assert.deepEqual(msgs[1].song, {
      song_id: '123', name: 'So What', artist: 'Miles Davis', mp3_url: 'http://x.mp3',
    });
  });

  it('music event ignores malformed data', () => {
    let msgs = newConversation('x');
    msgs = applyEvent(msgs, 'music', '{broken');
    assert.equal(msgs[1].song, null);
  });

  it('status searching sets placeholder, found clears it', () => {
    let msgs = newConversation('来一首晴天');
    msgs = applyEvent(msgs, 'status', '{"phase":"searching"}');
    assert.equal(msgs[1].text, 'Searching...');
    msgs = applyEvent(msgs, 'status', '{"phase":"found","name":"晴天"}');
    assert.equal(msgs[1].text, '');
  });

  it('done event finalizes streaming state', () => {
    let msgs = newConversation('x');
    msgs = applyEvent(msgs, 'done', '{}');
    assert.equal(msgs[1].state, 'done');
  });

  it('error event marks message with errorText', () => {
    let msgs = newConversation('x');
    msgs = applyEvent(msgs, 'error', '网络中断');
    assert.equal(msgs[1].state, 'error');
    assert.equal(msgs[1].errorText, '网络中断');
  });

  it('ignores events when list is empty', () => {
    assert.deepEqual(applyEvent([], 'text', 'x'), []);
  });
});