<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { usePlaylistStore } from '../stores/playlist'
import { useChatStore } from '../stores/chat'
import { usePlayerStore } from '../stores/player'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const playlist = usePlaylistStore()
const chat = useChatStore()
const player = usePlayerStore()

const tab = ref('session')

const currentSongId = computed(() => player.currentTrack?.song_id || '')

const songs = computed(() =>
  tab.value === 'session' ? [...playlist.order].reverse() : playlist.globalRecent,
)

watch(
  () => props.visible,
  (open) => {
    if (open) playlist.refreshGlobal()
  },
)

function play(songId) {
  chat.playLocalSong(songId)
}

function onKeydown(e) {
  if (e.key === 'Escape' && props.visible) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="drawer-scrim" v-show="visible" @click="emit('close')"></div>
  <aside
    class="history-drawer"
    :class="{ open: visible }"
    aria-label="播放历史"
    :aria-hidden="!visible"
  >
    <header class="drawer-header">
      <div class="drawer-tabs" role="tablist">
        <button
          role="tab"
          :aria-selected="tab === 'session'"
          :class="{ active: tab === 'session' }"
          @click="tab = 'session'"
        >
          本会话
        </button>
        <button
          role="tab"
          :aria-selected="tab === 'global'"
          :class="{ active: tab === 'global' }"
          @click="tab = 'global'"
        >
          最近
        </button>
      </div>
      <button class="drawer-close" @click="emit('close')" aria-label="关闭历史面板">✕</button>
    </header>

    <p v-if="!songs.length" class="drawer-empty">
      {{ tab === 'session' ? '本会话还没有播放记录' : '还没有历史记录' }}
    </p>

    <ul class="drawer-list" v-else>
      <li
        v-for="song in songs"
        :key="song.song_id"
        :class="{ current: song.song_id === currentSongId }"
      >
        <button @click="play(song.song_id)" :aria-label="`播放 ${song.name}`">
          <span class="drawer-cover">
            <img v-if="song.cover_url" :src="song.cover_url" alt="" loading="lazy" />
            <span v-else aria-hidden="true">♪</span>
          </span>
          <span class="drawer-meta">
            <b>{{ song.name }}</b>
            <i>{{ song.artist }}</i>
          </span>
          <span class="drawer-play" aria-hidden="true">
            {{ song.song_id === currentSongId ? '●' : '▶' }}
          </span>
        </button>
      </li>
    </ul>
  </aside>
</template>
