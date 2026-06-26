import { describe, it, beforeEach } from 'node:test';
import { strict as assert } from 'node:assert';
import { enqueueSpeech, stopSpeech } from '../../frontend/src/stores/speech-queue.js';

describe('SpeechQueue', () => {
  beforeEach(() => {
    stopSpeech();  // reset state between tests
  });

  it('enqueueSpeech does not throw when music is playing', () => {
    enqueueSpeech(['http://example.com/hello.mp3'], true);
    // musicIsPlaying=true → silently dropped, no error
  });

  it('enqueueSpeech does not throw with empty urls', () => {
    enqueueSpeech([], false);
    enqueueSpeech(null, false);
  });

  it('stopSpeech clears without error when idle', () => {
    stopSpeech();
    // should not throw
  });

  it('enqueueSpeech with urls when music is off does not throw', () => {
    // This will attempt playback (which fails in test env since there's no DOM Audio),
    // but should handle the rejection gracefully.
    enqueueSpeech(['http://localhost/never-exists.mp3'], false);
  });
});
