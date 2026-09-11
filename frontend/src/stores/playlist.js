// 会话歌单 + 跨会话最近播放（RFC-020 契约 B/C 的前端侧）
// 数据源：GET /api/v1/agent/history；回放：POST /api/v1/agent/replay（chat store）

import { defineStore } from 'pinia'
import { nextInList, nextInShuffle, makeShuffle, toPlayOrder } from './queue.js'

export const SESSION_LIMIT = 50
export const GLOBAL_LIMIT = 100

function pickMeta(song) {
  return {
    song_id: song.song_id,
    name: song.name || '',
    artist: song.artist || '',
    cover_url: song.cover_url || '',
    album: song.album || '',
    duration_ms: song.duration_ms ?? null,
  }
}

export const usePlaylistStore = defineStore('playlist', {
  state: () => ({
    order: [],          // 本会话歌单（播放顺序：旧 → 新）
    globalRecent: [],   // 跨会话最近播放（新 → 旧）
    shuffle: [],        // 洗牌队列（song_id 数组）
    loading: false,
    error: '',
  }),

  getters: {
    size: (state) => state.order.length,
    hasSongs: (state) => state.order.length > 0,
  },

  actions: {
    /** 会话歌单（进页面时同步一次；music 事件实时入列） */
    async syncSession(sessionId) {
      if (!sessionId) return
      try {
        const res = await fetch(
          `/api/v1/agent/history?scope=session&session_id=${encodeURIComponent(sessionId)}&limit=${SESSION_LIMIT}`,
        )
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        this.order = toPlayOrder((data.songs || []).map(pickMeta))
      } catch (error) {
        // 历史不可用不阻断播放：本地从空开始，靠 music 事件累积
        console.warn('Session history sync failed:', error)
      }
    },

    /** 跨会话最近播放（历史面板打开时刷新） */
    async refreshGlobal() {
      this.loading = true
      this.error = ''
      try {
        const res = await fetch(`/api/v1/agent/history?scope=global&limit=${GLOBAL_LIMIT}`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        this.globalRecent = (data.songs || []).map(pickMeta)
      } catch (error) {
        this.error = '历史加载失败'
        console.warn('Global history fetch failed:', error)
      } finally {
        this.loading = false
      }
    },

    /** music 事件到达 → 去重后追加到队尾（旧→新），本地 cap 50 */
    registerPlay(song) {
      if (!song?.song_id) return
      const meta = pickMeta(song)
      this.order = this.order.filter((s) => s.song_id !== meta.song_id)
      this.order.push(meta)
      if (this.order.length > SESSION_LIMIT) {
        this.order.splice(0, this.order.length - SESSION_LIMIT)
      }
      if (this.shuffle.length && !this.shuffle.includes(meta.song_id)) {
        this.shuffle.push(meta.song_id)
      }
    },

    ensureShuffle(rand = Math.random) {
      const ids = this.order.map((s) => s.song_id)
      const stale =
        this.shuffle.length !== ids.length ||
        ids.some((id) => !this.shuffle.includes(id))
      if (stale) this.shuffle = makeShuffle(ids, rand)
      return this.shuffle
    },

    /** 列表模式下一首（song_id）；缓存不足（<2 首）返回 null → 调用方回退 Agent */
    nextSongId(mode, currentId) {
      if (this.order.length < 2) return null
      const ids = this.order.map((s) => s.song_id)
      if (mode === 'list-shuffle') {
        this.ensureShuffle()
        const result = nextInShuffle(this.shuffle, currentId)
        if (!result) return null
        this.shuffle = result.shuffle
        return result.songId
      }
      return nextInList(ids, currentId)
    },

    /** 会话内已有该曲（历史面板高亮"当前"之外的判断辅助） */
    findMeta(songId) {
      return this.order.find((s) => s.song_id === songId) || null
    },
  },
})
