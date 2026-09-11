<script setup>
import { ref, watch, nextTick } from 'vue'
import { useChatStore } from '../stores/chat'
import { sendFeedback } from '../stores/feedback'

const chat = useChatStore()

// 已标记不喜欢的歌曲（组件内状态，页面刷新即重置）
const dislikedIds = ref(new Set())

function onDislike(song) {
  if (!song?.song_id || dislikedIds.value.has(song.song_id)) return
  sendFeedback('song_disliked', song)
  dislikedIds.value.add(song.song_id)
}

const EXAMPLES = [
  '来首轻松的',
  '推荐适合下雨天的歌',
  '上次那种感觉的，再放一次',
]

// ── 消息流自动跟随（用户上滚时不打扰） ──
const listRef = ref(null)
const pinned = ref(true)

function onScroll() {
  const el = listRef.value
  if (!el) return
  pinned.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

async function scrollToBottom() {
  if (!pinned.value) return
  await nextTick()
  const el = listRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(() => chat.messages.length, scrollToBottom)
watch(() => chat.messages[chat.messages.length - 1]?.text, scrollToBottom)
</script>

<template>
  <main class="chat-area">
    <!-- 空态引导 -->
    <div class="empty-state" v-if="chat.messages.length === 0">
      <p class="empty-title">你的私人 AI DJ 已就位</p>
      <p class="empty-hint">试试说：</p>
      <div class="empty-examples">
        <button
          v-for="example in EXAMPLES"
          :key="example"
          class="example-chip"
          @click="chat.sendCommand(example)"
        >
          {{ example }}
        </button>
      </div>
      <p class="empty-shortcut">Ctrl+K 或 / 聚焦输入 · 空格播放/暂停</p>
    </div>

    <!-- 消息流 -->
    <div class="msg-list" v-else ref="listRef" @scroll.passive="onScroll">
      <div
        v-for="msg in chat.messages"
        :key="msg.id"
        class="msg"
        :class="[`msg-${msg.role}`, { 'msg-streaming': msg.state === 'streaming', 'msg-error': msg.state === 'error' }]"
      >
        <div class="msg-text">
          <!-- 搜索/调谐状态（结构化，不再塞进正文） -->
          <template v-if="msg.state === 'streaming' && !msg.text && msg.status">
            <div class="status-line" :class="{ found: msg.status === 'found' }">
              <span class="status-pulse"></span>
              {{ msg.status === 'searching' ? 'SEARCHING' : 'TUNING IN' }}
            </div>
          </template>
          <template v-else-if="msg.state === 'streaming' && !msg.text">
            <span class="typing-dots"><i></i><i></i><i></i></span>
          </template>
          <template v-else>
            {{ msg.text }}<span v-if="msg.state === 'streaming'" class="cursor"></span>
          </template>
        </div>

        <!-- 歌曲卡片 -->
        <div class="song-card" v-if="msg.song">
          <div class="song-card-cover">
            <img v-if="msg.song.cover_url" :src="msg.song.cover_url" :alt="`${msg.song.name} 封面`" loading="lazy" />
            <span v-else class="cover-fallback" aria-hidden="true">♪</span>
          </div>
          <div class="song-card-body">
            <div class="song-card-label">
              <template v-if="msg.auto"><span class="auto-tag">AUTO</span> 电台续播</template>
              <template v-else>NOW PLAYING</template>
            </div>
            <div class="song-card-title">{{ msg.song.name }}</div>
            <div class="song-card-artist">{{ msg.song.artist }}</div>
          </div>
          <div class="song-card-actions">
            <button
              class="song-dislike-btn"
              :class="{ marked: dislikedIds.has(msg.song.song_id) }"
              :aria-pressed="dislikedIds.has(msg.song.song_id)"
              @click="onDislike(msg.song)"
            >
              {{ dislikedIds.has(msg.song.song_id) ? '✕ 已记住' : '✕ 不喜欢' }}
            </button>
          </div>
        </div>

        <!-- 错误块 -->
        <div class="error-block" v-if="msg.state === 'error'">
          <div class="error-label">SIGNAL LOST</div>
          <div class="error-text">{{ msg.errorText }}</div>
        </div>
      </div>
    </div>
  </main>
</template>
