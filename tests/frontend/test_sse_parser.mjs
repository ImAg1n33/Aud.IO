import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';
import { SSEParser } from '../../frontend/src/stores/sse-parser.js';

describe('SSEParser', () => {
  it('parses a complete event in a single chunk', () => {
    const parser = new SSEParser();
    const events = parser.feed('event: token\ndata: hello\n\n');
    assert.equal(events.length, 1);
    assert.equal(events[0].event, 'token');
    assert.equal(events[0].data, 'hello');
  });

  it('handles event split across two chunks', () => {
    const parser = new SSEParser();
    const first = parser.feed('event: music\ndata: {"name":"So ');
    assert.equal(first.length, 0);  // incomplete block

    const second = parser.feed('What"}\n\n');
    assert.equal(second.length, 1);
    assert.equal(second[0].event, 'music');
    assert.equal(second[0].data, '{"name":"So What"}');
  });

  it('handles multiple complete events in one chunk', () => {
    const parser = new SSEParser();
    const events = parser.feed(
      'event: status\ndata: {"phase":"found"}\n\nevent: music\ndata: {"song":1}\n\n'
    );
    assert.equal(events.length, 2);
  });

  it('handles multi-line data', () => {
    const parser = new SSEParser();
    const events = parser.feed('event: done\ndata: line1\ndata: line2\n\n');
    assert.equal(events.length, 1);
    assert.equal(events[0].data, 'line1\nline2');
  });

  it('ignores blocks without event type', () => {
    const parser = new SSEParser();
    const events = parser.feed('data: orphan\n\n');
    assert.equal(events.length, 0);
  });

  it('reset clears accumulated buffer', () => {
    const parser = new SSEParser();
    parser.feed('event: token\ndata: hel');
    parser.reset();
    const events = parser.feed('lo\n\n');
    assert.equal(events.length, 0);  // buffer was cleared, "lo\n\n" is orphaned
  });
});
