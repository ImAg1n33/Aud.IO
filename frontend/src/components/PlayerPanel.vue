<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { usePlayerStore } from '../stores/player'
import { useChatStore } from '../stores/chat'
import { usePlaylistStore } from '../stores/playlist'
import { MODES, MODE_LABELS, MODE_SHORT, isListMode } from '../stores/queue.js'

const player = usePlayerStore()
const chat = useChatStore()
const playlist = usePlaylistStore()

// ── 可视化：点阵频谱（reduced-motion 时静态） ──
const BARS = 14
const VIZ_DOTS = 7
const bars = ref(new Array(BARS).fill(0.04))
const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
let rafId = null

function tick() {
  const target = player.spectrum(BARS)
  bars.value = bars.value.map((prev, i) => Math.max(target[i], prev * 0.84))
  rafId = requestAnimationFrame(tick)
}

onMounted(() => {
  if (!reducedMotion) rafId = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  if (rafId) cancelAnimationFrame(rafId)
})

// ── 控制 ──
function onNext() {
  if (isListMode(player.playMode)) {
    const nextId = playlist.nextSongId(player.playMode, player.currentTrack?.song_id)
    if (nextId) {
      chat.playLocalSong(nextId)
      return
    }
  }
  chat.sendCommand('换一首')
}

function onPrev() {
  chat.sendCommand('上一首')
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

function onSeekKey(e) {
  const step = (player.duration || 0) * 0.02
  if (e.key === 'ArrowRight') {
    e.preventDefault()
    player.seek(Math.min(player.duration || 0, player.currentTime + step))
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    player.seek(Math.max(0, player.currentTime - step))
  }
}
</script>

<template>
  <section class="player" :class="{ playing: player.isPlaying }" aria-label="播放器">
    <!-- 待机态：与播放态等高，零布局跳变 -->
    <div class="player-standby" v-if="!player.hasTrack">
      <span class="standby-matrix" aria-hidden="true"></span>
      <div>
        <p class="standby-title">STANDBY</p>
        <p class="standby-hint">说句话点一首，或按 / 开始</p>
      </div>
    </div>

    <template v-else>
      <div class="player-main">
        <div class="player-cover">
          <img
            v-if="player.currentTrack.cover_url"
            :src="player.currentTrack.cover_url"
            :alt="`${player.currentTrack.name} 封面`"
            loading="lazy"
          />
          <span v-else class="cover-fallback" aria-hidden="true">♪</span>
        </div>

        <div class="player-meta">
          <div class="player-label">
            <span>{{ player.playMode === 'loop' ? 'LOOPING' : 'NOW PLAYING' }}</span>
            <span class="mode-name">{{ MODE_LABELS[player.playMode] }}</span>
            <span v-if="player.isBuffering" class="buffering">BUFFERING</span>
          </div>
          <h2 class="player-title" :title="player.currentTrack.name">{{ player.currentTrack.name }}</h2>
          <p class="player-artist" :title="player.currentTrack.artist">
            {{ player.currentTrack.artist }}<template v-if="player.currentTrack.album"> · {{ player.currentTrack.album }}</template>
          </p>
        </div>

        <div class="viz" :class="{ active: player.isPlaying }" aria-hidden="true">
          <span v-for="(level, i) in bars" :key="i" class="viz-col">
            <i
              v-for="dot in VIZ_DOTS"
              :key="dot"
              :class="{ lit: dot <= Math.round(level * VIZ_DOTS) }"
            ></i>
          </span>
        </div>
      </div>

      <!-- 进度条 -->
      <div
        class="player-progress"
        role="slider"
        tabindex="0"
        aria-label="播放进度"
        :aria-valuemin="0"
        :aria-valuemax="Math.round(player.duration || 0)"
        :aria-valuenow="Math.round(player.currentTime || 0)"
        @click="onSeek"
        @keydown="onSeekKey"
      >
        <div
          class="player-progress-fill"
          :style="{ width: player.duration ? (player.currentTime / player.duration) * 100 + '%' : '0%' }"
        ></div>
        <div
          class="player-progress-thumb"
          :style="{ left: player.duration ? (player.currentTime / player.duration) * 100 + '%' : '0%' }"
        ></div>
      </div>
      <div class="player-times">
        <span>{{ formatTime(player.currentTime) }}</span>
        <span>{{ formatTime(player.duration) }}</span>
      </div>

      <div class="player-controls">
        <div class="mode-switch" role="group" aria-label="播放模式">
          <button
            v-for="m in MODES"
            :key="m"
            class="mode-btn"
            :class="{ active: player.playMode === m }"
            :title="MODE_LABELS[m]"
            :aria-pressed="player.playMode === m"
            @click="player.setMode(m)"
          >
            {{ MODE_SHORT[m] }}
          </button>
        </div>

        <button class="ctrl-btn" @click="onPrev" aria-label="上一首">PREV</button>
        <button
          class="ctrl-play"
          @click="player.togglePlay()"
          :aria-label="player.isPlaying ? '暂停' : '播放'"
        >
          {{ player.isPlaying ? '❚❚' : '▶' }}
        </button>
        <button class="ctrl-btn" @click="onNext" aria-label="下一首">NEXT</button>

        <div class="player-volume">
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            :value="player.volume"
            @input="player.setVolume(Number($event.target.value))"
            aria-label="音量"
          />
        </div>
      </div>
    </template>
  </section>
</template>
