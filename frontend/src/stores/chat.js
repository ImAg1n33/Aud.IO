import { defineStore } from 'pinia'
import { usePlayerStore } from './player'
import { SSEParser } from './sse-parser'
import { enqueueSpeech, stopSpeech } from './speech-queue'

// ── Internal: typewriter engine (hidden from components) ──

let typeQueue = []
let typeTimer = null
const TYPE_SPEED = 28 // ms per character

function flushTypeQueue(store) {
  if (typeQueue.length === 0) {
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
    return
  }
  const chars = typeQueue.splice(0, 2)
  store.responseText += chars.join('')
  if (typeQueue.length === 0) {
    clearInterval(typeTimer)
    typeTimer = null
  }
}

function startTyping(store) {
  if (!typeTimer && typeQueue.length > 0) {
    typeTimer = setInterval(() => flushTypeQueue(store), TYPE_SPEED)
  }
}

function resetTyping() {
  typeQueue = []
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
}

// ── Internal: SSE event handler ──

function handleSSE(event, data, store, player) {
  switch (event) {
    case 'token':
      store.responseColor = 'var(--text-primary)'
      for (const ch of data) {
        typeQueue.push(ch)
      }
      startTyping(store)
      break

    case 'text':
      if (store.responseText === '> ') {
        store.responseColor = 'var(--text-primary)'
        store.responseText = '> ' + data
      }
      break

    case 'status':
      try {
        const status = JSON.parse(data)
        if (status.phase === 'searching') {
          store.responseText = '> Searching...'
          store.responseColor = 'var(--text-disabled)'
        } else if (status.phase === 'found' || status.phase === 'not_found') {
          // Reset for the incoming stream — wipe the "Searching..." prefix
          resetTyping()
          store.responseText = '> '
          store.responseColor = 'var(--text-primary)'
        }
      } catch (e) { /* ignore malformed status */ }
      break

    case 'music':
      try {
        const music = JSON.parse(data)
        if (music.mp3_url) {
          player.playTrack(music)
        }
      } catch (e) {
        console.error('Failed to parse music data:', e)
      }
      break

    case 'speech':
      try {
        const speech = JSON.parse(data)
        enqueueSpeech(speech.urls, player.isPlaying)
      } catch (e) {
        console.error('Failed to parse speech data:', e)
      }
      break

    case 'done':
      try {
        const reply = JSON.parse(data)
        store.jsonDump = JSON.stringify(reply, null, 2)
      } catch (e) {
        console.error('Failed to parse done data:', e)
      }
      break

    case 'error':
      store.responseColor = 'var(--accent)'
      store.responseText += '\n> ERROR: ' + data
      break
  }
}

// ── Store ──

export const useChatStore = defineStore('chat', {
  state: () => ({
    userInput: '',
    responseText: '[READY]',
    responseColor: 'var(--text-disabled)',
    isProcessing: false,
    jsonDump: '',
  }),

  actions: {
    async sendCommand(sessionId, currentPlayingTrack) {
      const text = this.userInput.trim()
      if (!text) return

      const player = usePlayerStore()

      this.userInput = ''
      this.isProcessing = true
      this.responseColor = 'var(--text-disabled)'
      this.responseText = '> '
      resetTyping()

      // Stop current playback and speech before making a new request
      player.stopTrack()
      stopSpeech()

      try {
        const response = await fetch('/api/v1/agent/respond/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_input: text,
            context: { 'Currently Playing': currentPlayingTrack },
            session_id: sessionId,
          }),
        })

        if (!response.ok) throw new Error('SERVER REJECTED CONNECTION.')

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const parser = new SSEParser()

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          for (const ev of parser.feed(chunk)) {
            handleSSE(ev.event, ev.data, this, player)
          }
        }
      } catch (error) {
        this.responseColor = 'var(--accent)'
        this.responseText = '> CONNECTION ERROR: ' + error.message
      } finally {
        this.isProcessing = false
      }
    },
  },
})
