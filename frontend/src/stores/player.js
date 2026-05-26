import { defineStore } from 'pinia'

// Non-reactive internals — Web Audio API objects don't benefit from Vue reactivity
let audioCtx = null
let fadeGain = null
let fadeTimer = null
let audioElement = null

export const usePlayerStore = defineStore('player', {
  state: () => ({
    isPlaying: false,
    playMode: 'dj',           // 'dj' | 'loop' | 'list'
    currentTrack: /** @type {{name:string, artist:string, mp3_url:string}|null} */ (null),
    trackDisplay: '///',
  }),

  getters: {
    hasTrack: (state) => state.currentTrack !== null,
  },

  actions: {
    // ── AudioContext lifecycle ──

    /** Bind the <audio> element and initialise Web Audio graph. */
    attachAudio(el) {
      audioElement = el
      if (!audioCtx && audioElement) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)()
        const source = audioCtx.createMediaElementSource(audioElement)
        const gain = audioCtx.createGain()
        source.connect(gain)
        gain.connect(audioCtx.destination)
        fadeGain = gain
      }
    },

    /** Ensure AudioContext is running (browsers suspend it without user gesture). */
    async _ensureAudioRunning() {
      if (audioCtx && audioCtx.state === 'suspended') {
        await audioCtx.resume()
      }
    },

    // ── Fade helpers ──

    _fadeOut(duration = 1.5) {
      if (!fadeGain) return
      if (fadeTimer) clearTimeout(fadeTimer)
      fadeGain.gain.setValueAtTime(fadeGain.gain.value, audioCtx.currentTime)
      fadeGain.gain.linearRampToValueAtTime(0, audioCtx.currentTime + duration)
    },

    _fadeIn(duration = 0.8) {
      if (!fadeGain) return
      if (fadeTimer) clearTimeout(fadeTimer)
      fadeGain.gain.setValueAtTime(0, audioCtx.currentTime)
      fadeGain.gain.linearRampToValueAtTime(1, audioCtx.currentTime + duration)
    },

    // ── Playback controls ──

    /** Start playing a new track. Called from chat store on SSE "music" event. */
    async playTrack(track) {
      if (!track?.mp3_url) return
      if (!audioElement) return
      this.attachAudio(audioElement)
      await this._ensureAudioRunning()

      // Cancel any pending fades (from stopTrack) and reset gain immediately
      if (fadeGain) {
        fadeGain.gain.cancelScheduledValues(audioCtx.currentTime)
        fadeGain.gain.setValueAtTime(1, audioCtx.currentTime)
      }

      audioElement.src = track.mp3_url
      audioElement.play().catch((e) => {
        console.error('Audio playback failed:', e.name, e.message)
      })
      this.isPlaying = true
      this.currentTrack = track
      this.trackDisplay = `♪ ${track.name} - ${track.artist} ♪`
    },

    stopTrack() {
      if (!audioElement) return
      this._fadeOut(1.2)
      fadeTimer = setTimeout(() => {
        audioElement.pause()
        audioElement.currentTime = 0
        this.isPlaying = false
      }, 1300)
    },

    async togglePlay() {
      if (!audioElement) return
      this.attachAudio(audioElement)
      if (this.isPlaying) {
        this._fadeOut(0.5)
        fadeTimer = setTimeout(() => {
          audioElement.pause()
          this.isPlaying = false
        }, 600)
      } else {
        await this._ensureAudioRunning()
        audioElement.play()
        this._fadeIn(0.3)
        this.isPlaying = true
      }
    },

    toggleMode() {
      const modes = ['dj', 'loop', 'list']
      const idx = modes.indexOf(this.playMode)
      this.playMode = modes[(idx + 1) % modes.length]
    },

    prevTrack() {
      console.log('Previous track — pending')
    },

    nextTrack() {
      console.log('Next track — pending')
    },

    onEnded() {
      this.isPlaying = false
      if (this.playMode === 'dj') {
        console.log('DJ mode: song ended, ready for next AI pick')
      }
    },
  },
})
