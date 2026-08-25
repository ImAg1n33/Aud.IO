import { defineStore } from 'pinia'
import { usePlayerStore } from './player.js'
import { SSEParser } from './sse-parser.js'
import { enqueueSpeech, stopSpeech } from './speech-queue.js'

// ═══════════════════════════════════════════════════════════
// 纯消息 reducer —— 不依赖 DOM/Pinia，Node 可直接测试
// ═══════════════════════════════════════════════════════════

let _seq = 0
const _nextId = () => `m${++_seq}`

export function createUserMessage(text) {
  return { id: _nextId(), role: 'user', text, song: null, state: 'done', errorText: '' }
}

export function createDJMessage() {
  return { id: _nextId(), role: 'dj', text: '', song: null, state: 'streaming', errorText: '' }
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
      // 完整文案（覆盖 Searching... 占位，清空打字机队列由调用方处理）
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
      if (last.role !== 'dj') return msgs
      try {
        const status = JSON.parse(data)
        if (status.phase === 'searching') {
          last.text = 'Searching...'
        } else if (status.phase === 'found' || status.phase === 'not_found') {
          if (last.text === 'Searching...') last.text = ''
        }
      } catch (e) {
        /* 忽略畸形 status */
      }
      return msgs
    }

    case 'done':
      if (last.role === 'dj') last.state = 'done'
      return msgs

    case 'error':
      if (last.role === 'dj') {
        last.state = 'error'
        last.errorText = data
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
// Store
// ═══════════════════════════════════════════════════════════

export const useChatStore = defineStore('chat', {
  state: () => ({
    sessionId: '',
    userInput: '',
    messages: [],
    isProcessing: false,
    jsonDump: '',
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

      this.userInput = ''
      this.isProcessing = true
      resetTyping()

      // 新请求：停止当前播放（触发切歌反馈）并推送消息
      player.stopTrack()
      stopSpeech()
      this.messages.push(createUserMessage(text))
      this.messages.push(createDJMessage())

      const currentlyPlaying = player.currentTrack
        ? `${player.currentTrack.artist} - ${player.currentTrack.name}`
        : 'None'

      try {
        const response = await fetch('/api/v1/agent/respond/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_input: text,
            context: { 'Currently Playing': currentlyPlaying },
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

    /** SSE 事件 → 消息流 + 播放器/语音联动 */
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

        case 'error':
          resetTyping()
          this.messages = applyEvent(this.messages, event, data)
          break

        default:
          break
      }
    },
  },
})