/**
 * SpeechQueue — plays TTS audio segments sequentially.
 *
 * Music takes priority: if the player is already playing a track, incoming
 * speech is silently dropped so it never interrupts the music.
 */

let _audio = null
let _queue = []
let _playing = false

function _playNext() {
  if (_playing || _queue.length === 0) return
  // Audio API is browser-only — silently skip in non-browser environments
  if (typeof Audio === 'undefined') {
    _queue = []
    return
  }
  _playing = true
  const url = _queue.shift()
  const audio = new Audio(url)
  _audio = audio

  audio.onended = () => {
    _playing = false
    _audio = null
    _playNext()
  }
  audio.onerror = () => {
    console.warn('Speech audio failed to load:', url)
    _playing = false
    _audio = null
    _playNext()
  }
  audio.play().catch((e) => {
    console.warn('Speech playback blocked:', e.message)
    _playing = false
    _audio = null
    _playNext()
  })
}

/**
 * Add speech URLs to the play queue.
 * @param {string[]} urls
 * @param {boolean} musicIsPlaying — if true, skip speech entirely
 */
export function enqueueSpeech(urls, musicIsPlaying) {
  if (musicIsPlaying) return  // music always wins in v0.4
  if (!urls || urls.length === 0) return
  _queue.push(...urls)
  _playNext()
}

/** Interrupt any in-progress speech and clear the queue. */
export function stopSpeech() {
  if (_audio) {
    _audio.pause()
    _audio = null
  }
  _queue = []
  _playing = false
}
