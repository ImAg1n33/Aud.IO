import { defineStore } from 'pinia'
import { usePlayerStore } from './player.js'
import { usePlaylistStore } from './playlist.js'
import { SSEParser } from './sse-parser.js'
import { enqueueSpeech, stopSpeech } from './speech-queue.js'

// ═══════════════════════════════════════════════════════════
// 纯消息 reducer —— 不依赖 DOM/Pinia，Node 可直接测试
// ═══════════════════════════════════════════════════════════

let _seq = 0
const _nextId = () => `m${++_seq}`

export function createUserMessage(text) {
  return { id: _nextId(), role: 'user', text, song: null, state: 'done', errorText: '', status: null, auto: false }
}

export function createDJMessage({ auto = false } = {}) {
  return { id: _nextId(), role: 'dj', text: '', song: null, state: 'streaming', errorText: '', status: null, auto }
}

/**
 * 自动续播消息复用决策（纯函数，可测）：
 * 仅复用「上一条失败的自动消息」做重试，避免聊天流刷屏；
 * 成功的自动消息（done）绝不复用——否则下一首会覆盖上一首的内容。
 */
export function shouldReuseAutoMessage(last) {
  return Boolean(last && last.role === 'dj' && last.auto && last.state === 'error')
}

/**
 * 将一条 SSE 事件应用到消息列表（返回新数组，最后一则 dj 消息被更新）。
 * token 事件不在此处理——打字机由 store 的 appendToken 逐字符推进。
 */
export function applyEvent(messages, event, data) {
  if (messages.length === 0) return messages
  const last = { ...messages[messages.length - 1] }
  const msgs = [...messages.slice(0, -1), last]

  switch (event) {
    case 'text':
      // 完整文案（覆盖静默预取路径的收集文本）
      if (last.role === 'dj') {
        last.text = data
        last.state = last.state === 'error' ? last.state : 'streaming'
      }
      return msgs

    case 'music': {
      if (last.role !== 'dj') return msgs
      try {
        last.song = JSON.parse(data)
      } catch (e) {
        /* 忽略畸形 music 数据 */
      }
      return msgs
    }

    case 'status': {
      // 结构化状态（searching / found / not_found）—— UI 独立表达，不再塞进文本
      if (last.role !== 'dj') return msgs
      try {
        const status = JSON.parse(data)
        last.status = status.phase || null
      } catch (e) {
        /* 忽略畸形 status */
      }
      return msgs
    }

    case 'done':
      if (last.role === 'dj') {
        last.state = 'done'
        last.status = null
      }
      return msgs

    case 'error':
      if (last.role === 'dj') {
        last.state = 'error'
        last.errorText = data
        last.status = null
      }
      return msgs

    default:
      return msgs
  }
}

// ═══════════════════════════════════════════════════════════
// 打字机引擎（内部，不导出）
// ═══════════════════════════════════════════════════════════

let typeQueue = []
let typeTimer = null
const TYPE_SPEED = 28 // ms per character

function flushTypeQueue(store) {
  if (typeQueue.length === 0) {
    if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
    return
  }
  const chars = typeQueue.splice(0, 2)
  store.appendToken(chars.join(''))
  if (typeQueue.length === 0) {
    clearInterval(typeTimer)
    typeTimer = null
  }
}

function startTyping(store) {
  if (!typeTimer && typeQueue.length > 0) {
    typeTimer = setInterval(() => flushTypeQueue(store), TYPE_SPEED)
  }
}

function resetTyping() {
  typeQueue = []
  if (typeTimer) { clearInterval(typeTimer); typeTimer = null }
}

// ═══════════════════════════════════════════════════════════
// 自动续播重试（熔断）—— 模块级计时器
// ═══════════════════════════════════════════════════════════

const AUTO_MAX_RETRIES = 3
const AUTO_RETRY_DELAY = 4000
let autoRetryTimer = null

// ═══════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionId: '',
    userInput: '',
    messages: [],
    isProcessing: false,
    jsonDump: '',
    autoFailCount: 0,
  }),

  actions: {
    /** App 挂载时注入会话标识（localStorage 持久） */
    setSession(sessionId) {
      this.sessionId = sessionId
    },

    /** 打字机追加字符到当前 dj 消息 */
    appendToken(chars) {
      const last = this.messages[this.messages.length - 1]
      if (last && last.role === 'dj') {
        last.text += chars
      }
    },

    /** 发送消息（用户输入或快捷指令共用） */
    async sendCommand(text = this.userInput.trim()) {
      if (!text) return
      if (this.isProcessing) return

      const player = usePlayerStore()

      this._clearAutoRetry()
      this.autoFailCount = 0
      this.userInput = ''
      resetTyping()

      // 新请求：停止当前播放（触发切歌反馈）并推送消息
      player.stopTrack()
      stopSpeech()
      this.messages.push(createUserMessage(text))
      this.messages.push(createDJMessage())

      const currentlyPlaying = player.currentTrack
        ? `${player.currentTrack.artist} - ${player.currentTrack.name}`
        : 'None'
      const currentlyPlayingId = player.currentTrack?.song_id || ''

      await this._streamRequest(text, {
        'Currently Playing': currentlyPlaying,
        'Currently Playing ID': currentlyPlayingId,
      })
    },

    /**
     * DJ 模式自动续播（RFC-020 §2.5）—— 歌曲自然播完后触发。
     * 不伪造用户气泡；失败自动重试至多 AUTO_MAX_RETRIES 次后熔断。
     */
    async autoContinue() {
      if (this.isProcessing) return
      const player = usePlayerStore()
      if (player.isPlaying) return // 已被用户手动接管

      this._clearAutoRetry()

      // 复用上一条「失败的」自动消息（重试不刷屏）；成功消息不复用（否则覆盖上一首）
      const last = this.messages[this.messages.length - 1]
      const reuse = shouldReuseAutoMessage(last)
      if (reuse) {
        last.text = ''
        last.song = null
        last.state = 'streaming'
        last.errorText = ''
        last.status = null
      } else {
        this.messages.push(createDJMessage({ auto: true }))
      }
      resetTyping()

      await this._streamRequest('', { 'Auto Continue': 'true' })
    },

    /**
     * 列表模式本地直放（RFC-020 §2.5）—— 走 /replay 重解析 mp3，无 LLM。
     * 不推聊天消息：Now Playing 区 + 历史面板即反馈。
     */
    async playLocalSong(songId, { fromEnded = false } = {}) {
      if (!songId || this.isProcessing) return
      const player = usePlayerStore()

      // 用户主动切歌 → 上报 song_skipped（自然播完时 finishedReported 已拦截）
      if (!fromEnded) player.stopTrack()
      stopSpeech()

      this.isProcessing = true
      try {
        const response = await fetch('/api/v1/agent/replay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: songId, session_id: this.sessionId }),
        })
        if (!response.ok) throw new Error(`REPLAY FAILED (${response.status})`)
        const song = await response.json()
        player.playTrack(song)
      } catch (error) {
        console.error('Local replay failed:', error)
      } finally {
        this.isProcessing = false
      }
    },

    /** SSE 请求公共链路（发送 / 自动续播共用） */
    async _streamRequest(text, context) {
      const player = usePlayerStore()
      this.isProcessing = true
      try {
        const response = await fetch('/api/v1/agent/respond/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_input: text,
            context,
            session_id: this.sessionId,
          }),
        })

        if (!response.ok) throw new Error('SERVER REJECTED CONNECTION.')

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const parser = new SSEParser()

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          for (const ev of parser.feed(chunk)) {
            this.handleSSE(ev.event, ev.data, player)
          }
        }
      } catch (error) {
        this.handleSSE('error', error.message, player)
      } finally {
        this.isProcessing = false
      }
    },

    /** SSE 事件 → 消息流 + 播放器/语音/歌单联动 */
    handleSSE(event, data, player) {
      switch (event) {
        case 'token':
          // 逐字符入队，打字机推进（text 事件到达时队列会被清空）
          for (const ch of data) {
            typeQueue.push(ch)
          }
          startTyping(this)
          break

        case 'text':
          // 完整文案已含全部字符——清空打字机队列避免重复
          resetTyping()
          this.messages = applyEvent(this.messages, event, data)
          break

        case 'music': {
          const before = this.messages.length
          this.messages = applyEvent(this.messages, event, data)
          const last = this.messages[this.messages.length - 1]
          if (last && last.song && last.song.mp3_url) {
            usePlaylistStore().registerPlay(last.song)
            this.autoFailCount = 0
            player.playTrack(last.song)
          }
          if (this.messages.length !== before) return
          break
        }

        case 'status':
          this.messages = applyEvent(this.messages, event, data)
          break

        case 'speech':
          try {
            const speech = JSON.parse(data)
            enqueueSpeech(speech.urls, player.isPlaying)
          } catch (e) {
            console.error('Failed to parse speech data:', e)
          }
          break

        case 'done':
          this.messages = applyEvent(this.messages, event, data)
          try {
            this.jsonDump = JSON.stringify(JSON.parse(data), null, 2)
          } catch (e) {
            /* 忽略畸形 done */
          }
          break

        case 'error': {
          resetTyping()
          this.messages = applyEvent(this.messages, event, data)
          const last = this.messages[this.messages.length - 1]
          if (last && last.role === 'dj' && last.auto) {
            this._scheduleAutoRetry()
          }
          break
        }

        default:
          break
      }
    },

    /** 自动续播失败 → 延迟重试；连续失败达上限 → 熔断（等用户交互恢复） */
    _scheduleAutoRetry() {
      this.autoFailCount += 1
      if (this.autoFailCount >= AUTO_MAX_RETRIES) return
      this._clearAutoRetry()
      autoRetryTimer = setTimeout(() => {
        autoRetryTimer = null
        this.autoContinue()
      }, AUTO_RETRY_DELAY)
    },

    _clearAutoRetry() {
      if (autoRetryTimer) {
        clearTimeout(autoRetryTimer)
        autoRetryTimer = null
      }
    },
  },
})
