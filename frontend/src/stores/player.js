import { defineStore } from 'pinia'

export const usePlayerStore = defineStore('player', {
  state: () => ({
    playlist: [],        // 播放队列
    currentIndex: -1,    // 当前播放的索引
    isPlaying: false,    // 播放状态
    playMode: 'dj',      // 模式：'list' (顺序), 'random' (随机), 'loop' (单曲), 'dj' (AI 模式)
    historyLimit: 10,    // 历史黑名单长度
  }),
  
  getters: {
    currentTrack: (state) => state.playlist[state.currentIndex] || null,
    // 获取最近播放的歌曲名列表，作为发送给大模型的“黑名单”
    recentSongs: (state) => state.playlist.slice(-5).map(s => `${state.artist} - ${s.name}`)
  },

  actions: {
    // 当 AI 推荐了一首歌，我们把它塞进队列
    addToQueue(track) {
      this.playlist.push(track)
      if (!this.isPlaying) {
        this.playNext() // 如果当前没在播，直接开播
      }
    },
    playNext() {
      if (this.playMode === 'loop') {
        // 单曲循环逻辑
      } else if (this.playMode === 'random') {
        this.currentIndex = Math.floor(Math.random() * this.playlist.length)
      } else {
        this.currentIndex++
      }
      this.isPlaying = true
    },
    togglePlay() {
      this.isPlaying = !this.isPlaying
    }
  }
})