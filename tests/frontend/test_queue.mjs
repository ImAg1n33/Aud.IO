import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';
import {
  MODES,
  nextMode,
  isListMode,
  toPlayOrder,
  nextInList,
  makeShuffle,
  nextInShuffle,
} from '../../frontend/src/stores/queue.js';

describe('play mode helpers', () => {
  it('exposes 4 modes and cycles through them', () => {
    assert.deepEqual(MODES, ['dj', 'loop', 'list-loop', 'list-shuffle']);
    assert.equal(nextMode('dj'), 'loop');
    assert.equal(nextMode('loop'), 'list-loop');
    assert.equal(nextMode('list-loop'), 'list-shuffle');
    assert.equal(nextMode('list-shuffle'), 'dj');
  });

  it('isListMode only for list modes', () => {
    assert.equal(isListMode('list-loop'), true);
    assert.equal(isListMode('list-shuffle'), true);
    assert.equal(isListMode('dj'), false);
    assert.equal(isListMode('loop'), false);
  });
});

describe('session queue order', () => {
  it('toPlayOrder reverses history (newest-first → oldest-first)', () => {
    assert.deepEqual(toPlayOrder(['c', 'b', 'a']), ['a', 'b', 'c']);
    assert.deepEqual(toPlayOrder([]), []);
    assert.deepEqual(toPlayOrder(null), []);
  });

  it('nextInList cycles forward and wraps', () => {
    assert.equal(nextInList(['a', 'b', 'c'], 'a'), 'b');
    assert.equal(nextInList(['a', 'b', 'c'], 'c'), 'a');
    assert.equal(nextInList(['a', 'b', 'c'], 'unknown'), 'a');
    assert.equal(nextInList([], 'a'), null);
  });

  it('makeShuffle does not mutate input and keeps all ids', () => {
    const input = ['a', 'b', 'c', 'd'];
    const shuffled = makeShuffle(input, () => 0);
    assert.deepEqual(input, ['a', 'b', 'c', 'd']);
    assert.notEqual(shuffled, input);
    assert.deepEqual([...shuffled].sort(), ['a', 'b', 'c', 'd']);
  });

  it('nextInShuffle advances through the shuffle order', () => {
    const r = nextInShuffle(['x', 'y', 'z'], 'x');
    assert.equal(r.songId, 'y');
    assert.deepEqual(r.shuffle, ['x', 'y', 'z']);
  });

  it('nextInShuffle reshuffles at the end of a round', () => {
    const r = nextInShuffle(['x', 'y', 'z'], 'z', () => 0);
    assert.equal(r.songId, 'y'); // rand=0 → [y, z, x]
    assert.notEqual(r.shuffle, undefined);
  });

  it('nextInShuffle handles unknown current id and empty input', () => {
    assert.equal(nextInShuffle(['x', 'y', 'z'], 'nope').songId, 'x');
    assert.equal(nextInShuffle([], 'x'), null);
  });
});
