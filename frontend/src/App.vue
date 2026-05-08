<script setup>
import { ref, nextTick } from 'vue'

// Reactive state
const userInput = ref('')
const responseText = ref('> SYSTEM READY. AWAITING COMMAND.')
const responseColor = ref('#ffffff')
const isProcessing = ref(false)
const showPlayer = ref(false)
const showDebug = ref(false)
const trackName = ref('///')
const jsonDump = ref('')
const audioElement = ref(null)

let currentPlayingTrack = "None"

// Player control state
const isPlaying = ref(false)
const playMode = ref('dj')

const togglePlay = () => {
  if (!audioElement.value) return
  if (isPlaying.value) {
    audioElement.value.pause()
  } else {
    audioElement.value.play()
  }
  isPlaying.value = !isPlaying.value
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

// === Streaming sendCommand ===
const sendCommand = async () => {
  const text = userInput.value.trim()
  if (!text) return

  userInput.value = ''
  isProcessing.value = true
  responseColor.value = '#888'
  responseText.value = '> '
  showPlayer.value = false
  if (audioElement.value) audioElement.value.pause()

  try {
    const response = await fetch("http://127.0.0.1:8001/v1/agent/respond/stream", {
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

      // Parse SSE events from buffer
      const lines = buffer.split('\n')
      buffer = ""  // rebuild unconsumed portion

      let currentEvent = ""
      let currentData = ""

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith("data: ")) {
          currentData = line.slice(6)
        } else if (line === "" && currentEvent) {
          // End of event — process it
          handleSSE(currentEvent, currentData)
          currentEvent = ""
          currentData = ""
        } else if (line !== "") {
          // Continuation or incomplete — put back in buffer
          buffer += line + '\n'
        }
      }
    }

  } catch (error) {
    responseColor.value = 'var(--accent-red)'
    responseText.value = "> CONNECTION ERROR: " + error.message
  } finally {
    isProcessing.value = false
  }
}

const handleSSE = (event, data) => {
  switch (event) {
    case "token":
      // Raw streaming activity — show dots/progress
      if (responseText.value === '> ') {
        responseText.value = '> █'
      } else if (responseText.value.endsWith('█')) {
        responseText.value = responseText.value.slice(0, -1) + ' '
      } else if (responseText.value.endsWith(' ')) {
        responseText.value = responseText.value.slice(0, -1) + '█'
      }
      break

    case "text":
      // Clean answer text — replace the activity indicator
      responseColor.value = '#ffffff'
      responseText.value = "> " + data
      break

    case "music":
      try {
        const music = JSON.parse(data)
        if (music.mp3_url) {
          audioElement.value.src = music.mp3_url
          audioElement.value.play()
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
      responseColor.value = 'var(--accent-red)'
      responseText.value += "\n> ERROR: " + data
      break
  }
}
</script>

<template>
  <div class="container">
    <div class="header">Aud.IO</div>

    <div class="display-box">
      <span :style="{ color: responseColor }" style="white-space: pre-wrap;">{{ responseText }}</span>
      <span class="cursor" v-show="!isProcessing"></span>
    </div>

    <div class="input-group">
      <input
        type="text"
        v-model="userInput"
        @keypress.enter="sendCommand"
        placeholder="TYPE YOUR REQUEST..."
        autocomplete="off"
      >
      <button :disabled="isProcessing" @click="sendCommand">
        {{ isProcessing ? 'STREAMING...' : 'EXECUTE' }}
      </button>
    </div>

    <div class="player-panel" v-show="showPlayer">
      <div style="font-size: 1.2rem; color: #888; margin-bottom: 5px;">NOW PLAYING:</div>
      <div class="track-info">{{ trackName }}</div>

      <div class="control-board">
        <button class="ctrl-btn mode-btn" @click="toggleMode" title="Toggle mode">
          MODE: [{{ playMode.toUpperCase() }}]
        </button>
        <button class="ctrl-btn" @click="prevTrack" title="Previous">|◀◀</button>
        <button class="ctrl-btn play-btn" @click="togglePlay">
          {{ isPlaying ? '■ PAUSE' : '▶ PLAY' }}
        </button>
        <button class="ctrl-btn" @click="nextTrack" title="Next">▶▶|</button>
      </div>

      <audio ref="audioElement" @ended="onAudioEnded"></audio>
    </div>

    <div class="debug-panel" v-show="showDebug">
      <div style="font-size: 0.9rem; margin-bottom: 10px; border-bottom: 1px solid var(--accent-red); padding-bottom: 5px;">[ RAW DATA DUMP ]</div>
      <pre>{{ jsonDump }}</pre>
    </div>
  </div>
</template>