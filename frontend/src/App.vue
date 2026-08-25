<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { usePlayerStore } from './stores/player'
import { useChatStore } from './stores/chat'
import ChatPanel from './components/ChatPanel.vue'
import PlayerPanel from './components/PlayerPanel.vue'
import InputBar from './components/InputBar.vue'
import DebugPanel from './components/DebugPanel.vue'

// ── Session identity ──
const chat = useChatStore()
let sessionId = localStorage.getItem('aud_io_session')
if (!sessionId) {
  sessionId = crypto.randomUUID()
  localStorage.setItem('aud_io_session', sessionId)
}
chat.setSession(sessionId)

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
    const player = usePlayerStore()
    player.attachAudio(audioRef.value)
  }
})

// ── 键盘快捷键（仅桌面） ──
function onKeydown(e) {
  const target = e.target
  const isTyping = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')

  // Ctrl+K 或 '/' 聚焦输入框
  if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && !isTyping)) {
    e.preventDefault()
    const input = document.querySelector('.input-group input')
    if (input) input.focus()
    return
  }

  // 空格播放/暂停（输入中不响应）
  if (e.code === 'Space' && !isTyping && target?.tagName !== 'BUTTON') {
    e.preventDefault()
    usePlayerStore().togglePlay()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="app">
    <nav class="toolbar">
      <span class="brand">AUD.IO</span>
      <div class="toolbar-actions">
        <button class="toolbar-btn" @click="showDebug = !showDebug">
          {{ showDebug ? '[HIDE RAW]' : '[RAW]' }}
        </button>
        <button class="toolbar-btn" @click="toggleTheme">
          {{ theme === 'dark' ? '[LIGHT]' : '[DARK]' }}
        </button>
      </div>
    </nav>

    <ChatPanel />
    <PlayerPanel />
    <InputBar />

    <DebugPanel :visible="showDebug" />

    <audio
      ref="audioRef"
      @ended="usePlayerStore().onEnded()"
      crossorigin="anonymous"
    ></audio>
  </div>
</template>