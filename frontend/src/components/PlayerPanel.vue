<script setup>
import { usePlayerStore } from '../stores/player'
import { useChatStore } from '../stores/chat'

const player = usePlayerStore()
const chat = useChatStore()

const NEXT_CMD = '换一首'

function onNext() {
  // NEXT = 快捷指令走 agent（顺带触发 song_skipped 反馈）
  chat.sendCommand(NEXT_CMD)
}

function formatTime(seconds) {
  if (!seconds || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function onSeek(e) {
  const bar = e.currentTarget
  const rect = bar.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  player.seek(ratio * (player.duration || 0))
}
</script>

<template>
  <section class="player-panel" v-show="player.hasTrack">
    <div class="player-label">
      {{ player.playMode === 'loop' ? 'LOOPING' : 'NOW PLAYING' }}
      <span class="buffering-dot" v-if="player.isBuffering">●</span>
    </div>
    <div class="player-hero">{{ player.trackDisplay }}</div>

    <!-- 进度条 -->
    <div class="player-progress" @click="onSeek">
      <div
        class="player-progress-fill"
        :style="{ width: player.duration ? (player.currentTime / player.duration) * 100 + '%' : '0%' }"
      ></div>
    </div>
    <div class="player-times">
      <span>{{ formatTime(player.currentTime) }}</span>
      <span>{{ formatTime(player.duration) }}</span>
    </div>

    <div class="player-controls">
      <button class="ctrl-btn" @click="player.toggleMode()">MODE: {{ player.playMode.toUpperCase() }}</button>
      <button class="ctrl-btn" @click="player.prevTrack()">PREV</button>
      <button class="ctrl-btn ctrl-play" @click="player.togglePlay()">
        {{ player.isPlaying ? '■' : '▶' }}
      </button>
      <button class="ctrl-btn" @click="onNext">NEXT</button>

      <!-- 音量 -->
      <div class="player-volume">
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          :value="player.volume"
          @input="player.setVolume(Number($event.target.value))"
          aria-label="Volume"
        />
      </div>
    </div>
  </section>
</template>