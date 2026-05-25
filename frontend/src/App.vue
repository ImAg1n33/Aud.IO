<script setup>
import { ref, onMounted } from 'vue'
import { usePlayerStore } from './stores/player'
import ChatPanel from './components/ChatPanel.vue'
import PlayerPanel from './components/PlayerPanel.vue'
import InputBar from './components/InputBar.vue'
import DebugPanel from './components/DebugPanel.vue'

// ── Session identity ──
let sessionId = localStorage.getItem('aud_io_session')
if (!sessionId) {
  sessionId = crypto.randomUUID()
  localStorage.setItem('aud_io_session', sessionId)
}

// Track what's currently playing for context injection
let currentPlayingTrack = 'None'
const player = usePlayerStore()
// Watch for track changes (simple assignment, reactive in App context)
import { watch } from 'vue'
watch(() => player.currentTrack, (track) => {
  if (track) {
    currentPlayingTrack = `${track.artist} - ${track.name}`
  } else {
    currentPlayingTrack = 'None'
  }
})

// ── Theme ──
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

// ── Debug toggle ──
const showDebug = ref(false)

// ── Audio element ref (injected into player store) ──
const audioRef = ref(null)
onMounted(() => {
  if (audioRef.value) {
    player.attachAudio(audioRef.value)
  }
})
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

    <ChatPanel />
    <PlayerPanel />
    <InputBar :sessionId="sessionId" :currentPlayingTrack="currentPlayingTrack" />

    <DebugPanel :visible="showDebug" />

    <audio
      ref="audioRef"
      @ended="player.onEnded()"
      crossorigin="anonymous"
    ></audio>
  </div>
</template>
