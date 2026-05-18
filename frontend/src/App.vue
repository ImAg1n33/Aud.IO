<script setup>
import { ref, nextTick, onMounted } from 'vue'

// Reactive state
const userInput = ref('')
const responseText = ref('[READY]')
const responseColor = ref('var(--text-disabled)')
const isProcessing = ref(false)
const showPlayer = ref(false)
const showDebug = ref(false)
const trackName = ref('///')
const jsonDump = ref('')
const audioElement = ref(null)
const fadeGain = ref(null)  // AudioContext gain node for smooth transitions

let currentPlayingTrack = "None"
let audioCtx = null
let fadeTimer = null

const theme = ref('dark')
const toggleTheme = () => {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('theme', theme.value)
}
onMounted(() => {
  const saved = localStorage.getItem('theme')
  if (saved) {
    theme.value = saved
    document.documentElement.setAttribute('data-theme', saved)
  }
})

// Player control state
const isPlaying = ref(false)
const playMode = ref('dj')

const initAudioCtx = () => {
  if (!audioCtx && audioElement.value) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)()
    const source = audioCtx.createMediaElementSource(audioElement.value)
    const gain = audioCtx.createGain()
    source.connect(gain)
    gain.connect(audioCtx.destination)
    fadeGain.value = gain
  }
}

const fadeOut = (duration = 1.5) => {
  if (!fadeGain.value) return
  if (fadeTimer) clearTimeout(fadeTimer)
  const gain = fadeGain.value
  gain.gain.setValueAtTime(gain.gain.value, audioCtx.currentTime)
  gain.gain.linearRampToValueAtTime(0, audioCtx.currentTime + duration)
}

const fadeIn = (duration = 0.8) => {
  if (!fadeGain.value) return
  if (fadeTimer) clearTimeout(fadeTimer)
  const gain = fadeGain.value
  gain.gain.setValueAtTime(0, audioCtx.currentTime)
  gain.gain.linearRampToValueAtTime(1, audioCtx.currentTime + duration)
}

const stopAudio = () => {
  if (audioElement.value) {
    fadeOut(1.2)
    fadeTimer = setTimeout(() => {
      audioElement.value.pause()
      audioElement.value.currentTime = 0
      isPlaying.value = false
    }, 1300)
  }
}

const togglePlay = () => {
  if (!audioElement.value) return
  initAudioCtx()
  if (isPlaying.value) {
    fadeOut(0.5)
    fadeTimer = setTimeout(() => {
      audioElement.value.pause()
      isPlaying.value = false
    }, 600)
  } else {
    audioElement.value.play()
    fadeIn(0.3)
    isPlaying.value = true
  }
}

const toggleMode = () => {
  const modes = ['dj', 'loop', 'list']
  const currentIndex = modes.indexOf(playMode.value)
  playMode.value = modes[(currentIndex + 1) % modes.length]
}

const prevTrack = () => {
  console.log("Previous track — Pinia integration pending")
}

const nextTrack = () => {
  console.log("Next track — Pinia integration pending")
}

const onAudioEnded = () => {
  isPlaying.value = false
  if (playMode.value === 'dj') {
    console.log("DJ mode: song ended, ready for next AI pick")
  }
}

// === Streaming sendCommand with typewriter effect ===
const sendCommand = async () => {
  const text = userInput.value.trim()
  if (!text) return

  userInput.value = ''
  isProcessing.value = true
  responseColor.value = 'var(--text-disabled)'
  responseText.value = '> '
  showPlayer.value = false
  // Clear typewriter queue
  typeQueue = []
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }

  // Smooth fade-out instead of abrupt stop
  initAudioCtx()
  stopAudio()

  try {
    const response = await fetch("/api/v1/agent/respond/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_input: text,
        context: { "Currently Playing": currentPlayingTrack }
      })
    })

    if (!response.ok) throw new Error("SERVER REJECTED CONNECTION.")

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = ""

      let currentEvent = ""
      let currentData = ""

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith("data: ")) {
          currentData += (currentData ? '\n' : '') + line.slice(6)
        } else if (line === "" && currentEvent) {
          handleSSE(currentEvent, currentData)
          currentEvent = ""
          currentData = ""
        } else if (line !== "") {
          buffer += line + '\n'
        }
      }
    }

  } catch (error) {
    responseColor.value = 'var(--accent)'
    responseText.value = "> CONNECTION ERROR: " + error.message
  } finally {
    isProcessing.value = false
  }
}

// Typewriter queue — buffers incoming tokens and displays them at readable speed
let typeQueue = []
let typeTimer = null
const TYPE_SPEED = 28  // ms per character

const flushTypeQueue = () => {
  if (typeQueue.length === 0) {
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
    return
  }
  const chars = typeQueue.splice(0, 2)  // pop 1-2 chars per tick
  responseText.value += chars.join('')
  if (typeQueue.length === 0) {
    clearInterval(typeTimer)
    typeTimer = null
  }
}

const startTyping = () => {
  if (!typeTimer && typeQueue.length > 0) {
    typeTimer = setInterval(flushTypeQueue, TYPE_SPEED)
  }
}

const handleSSE = (event, data) => {
  switch (event) {
    case "token":
      // Buffer tokens and type out at controlled speed
      responseColor.value = 'var(--text-primary)'
      for (const ch of data) {
        typeQueue.push(ch)
      }
      startTyping()
      break

    case "text":
      // Clean answer fallback (if streaming missed tokens)
      if (responseText.value === '> ') {
        responseColor.value = 'var(--text-primary)'
        responseText.value = "> " + data
      }
      break

    case "music":
      try {
        const music = JSON.parse(data)
        if (music.mp3_url) {
          initAudioCtx()
          audioElement.value.src = music.mp3_url
          audioElement.value.play()
          fadeIn(0.6)
          isPlaying.value = true
          currentPlayingTrack = `${music.artist} - ${music.name}`
          trackName.value = `♪ ${music.name} - ${music.artist} ♪`
          showPlayer.value = true
        }
      } catch (e) {
        console.error("Failed to parse music data:", e)
      }
      break

    case "done":
      try {
        const reply = JSON.parse(data)
        jsonDump.value = JSON.stringify(reply, null, 2)
        showDebug.value = true
      } catch (e) {
        console.error("Failed to parse done data:", e)
      }
      break

    case "error":
      responseColor.value = 'var(--accent)'
      responseText.value += "\n> ERROR: " + data
      break
  }
}
</script>

<template>
  <div class="app">
    <nav class="toolbar">
      <span class="brand">AUD.IO</span>
      <div class="toolbar-actions">
        <button class="toolbar-btn" @click="showDebug = !showDebug">
          {{ showDebug ? '[HIDE]' : '[DEBUG]' }}
        </button>
        <button class="toolbar-btn" @click="toggleTheme">
          {{ theme === 'dark' ? '[LIGHT]' : '[DARK]' }}
        </button>
      </div>
    </nav>

    <main class="response-area">
      <p class="response-text" :style="{ color: responseColor }">{{ responseText }}<span class="cursor"></span></p>
    </main>

    <section class="player-panel" v-show="showPlayer">
      <div class="player-label">NOW PLAYING</div>
      <div class="player-hero">{{ trackName }}</div>
      <div class="player-controls">
        <button class="ctrl-btn" @click="toggleMode">MODE: {{ playMode.toUpperCase() }}</button>
        <button class="ctrl-btn" @click="prevTrack">PREV</button>
        <button class="ctrl-btn ctrl-play" @click="togglePlay">
          {{ isPlaying ? '■' : '▶' }}
        </button>
        <button class="ctrl-btn" @click="nextTrack">NEXT</button>
      </div>
    </section>

    <footer class="input-area">
      <div class="input-group">
        <input
          type="text"
          v-model="userInput"
          @keypress.enter="sendCommand"
          placeholder="What do you want to hear?"
          autocomplete="off"
        >
        <button class="send-btn" :disabled="isProcessing" @click="sendCommand">
          {{ isProcessing ? '...' : 'SEND' }}
        </button>
      </div>
    </footer>

    <div class="debug-panel" v-show="showDebug">
      <div class="debug-label">RAW DATA</div>
      <pre>{{ jsonDump }}</pre>
    </div>

    <audio ref="audioElement" @ended="onAudioEnded" crossorigin="anonymous"></audio>
  </div>
</template>