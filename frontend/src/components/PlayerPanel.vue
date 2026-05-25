<script setup>
import { usePlayerStore } from '../stores/player'

const player = usePlayerStore()
</script>

<template>
  <section class="player-panel" v-show="player.hasTrack">
    <div class="player-label">NOW PLAYING</div>
    <div class="player-hero">{{ player.trackDisplay }}</div>
    <div class="player-controls">
      <button class="ctrl-btn" @click="player.toggleMode()">MODE: {{ player.playMode.toUpperCase() }}</button>
      <button class="ctrl-btn" @click="player.prevTrack()">PREV</button>
      <button class="ctrl-btn ctrl-play" @click="player.togglePlay()">
        {{ player.isPlaying ? '■' : '▶' }}
      </button>
      <button class="ctrl-btn" @click="player.nextTrack()">NEXT</button>
    </div>
  </section>
</template>
