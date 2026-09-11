<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { usePlayerStore } from './stores/player'
import { useChatStore } from './stores/chat'
import { usePlaylistStore } from './stores/playlist'
import { isListMode } from './stores/queue.js'
import ChatPanel from './components/ChatPanel.vue'
import PlayerPanel from './components/PlayerPanel.vue'
import InputBar from './components/InputBar.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import DebugPanel from './components/DebugPanel.vue'

// ── Session identity ──
const chat = useChatStore()
const player = usePlayerStore()
const playlist = usePlaylistStore()

let sessionId = localStorage.getItem('aud_io_session')
if (!sessionId) {
  sessionId = crypto.randomUUID()
  localStorage.setItem('aud_io_session', sessionId)
}
chat.setSession(sessionId)

// ── Theme（防闪由 index.html 内联脚本负责，这里只做同步与切换） ──
const theme = ref('dark')

function applyTheme(next) {
  theme.value = next
  document.documentElement.setAttribute('data-theme', next)
  localStorage.setItem('theme', next)
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  if (saved === 'light' || saved === 'dark') theme.value = saved
  document.documentElement.setAttribute('data-theme', theme.value)
})

// ── Panels ──
const showDebug = ref(false)
const showHistory = ref(false)

// ── Audio element ref (injected into player store) ──
const audioRef = ref(null)
onMounted(() => {
  if (audioRef.value) {
    player.attachAudio(audioRef.value)
    playlist.syncSession(sessionId)
  }
})

/** 歌曲自然播完 → 模式编排（RFC-020 §2.5） */
function onTrackEnded() {
  const endedSongId = player.currentTrack?.song_id || ''
  player.onEnded()
  if (player.playMode === 'dj') {
    chat.autoContinue()
  } else if (isListMode(player.playMode)) {
    const nextId = playlist.nextSongId(player.playMode, endedSongId)
    if (nextId) chat.playLocalSong(nextId, { fromEnded: true })
  }
  // loop：audio.loop 处理，不会触发 ended
}

// ── 键盘快捷键（仅桌面） ──
function onKeydown(e) {
  const target = e.target
  const isTyping = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')
  const isButton = target?.tagName === 'BUTTON'

  // Ctrl+K 或 '/' 聚焦输入框
  if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && !isTyping)) {
    e.preventDefault()
    const input = document.querySelector('.input-group input')
    if (input) input.focus()
    return
  }

  // 空格播放/暂停（输入中/按钮聚焦时不响应）
  if (e.code === 'Space' && !isTyping && !isButton) {
    e.preventDefault()
    player.togglePlay()
  }

  // H 打开/关闭历史（输入中不响应）
  if (e.key.toLowerCase() === 'h' && !isTyping && !e.ctrlKey && !e.metaKey) {
    e.preventDefault()
    showHistory.value = !showHistory.value
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="app">
    <nav class="toolbar">
      <span class="brand">AUD<em>.</em>IO</span>
      <div class="toolbar-actions">
        <button
          class="toolbar-btn"
          :class="{ active: showHistory }"
          @click="showHistory = !showHistory"
        >
          [HISTORY]
        </button>
        <button class="toolbar-btn" @click="showDebug = !showDebug">
          {{ showDebug ? '[HIDE RAW]' : '[RAW]' }}
        </button>
        <button class="toolbar-btn" @click="toggleTheme">
          {{ theme === 'dark' ? '[LIGHT]' : '[DARK]' }}
        </button>
      </div>
    </nav>

    <PlayerPanel />
    <ChatPanel />
    <InputBar />

    <HistoryPanel :visible="showHistory" @close="showHistory = false" />
    <DebugPanel :visible="showDebug" />

    <audio
      ref="audioRef"
      @ended="onTrackEnded"
      crossorigin="anonymous"
    ></audio>
  </div>
</template>
