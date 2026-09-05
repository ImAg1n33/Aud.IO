<script setup>
import { ref } from 'vue'
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
</script>

<template>
  <main class="chat-area">
    <!-- 空态引导 -->
    <div class="empty-state" v-if="chat.messages.length === 0">
      <p class="empty-title">你的私人 AI DJ 已就位</p>
      <p class="empty-hint">试试说：</p>
      <p class="empty-examples">「来首轻松的」 · 「推荐适合下雨天的歌」 · 「上次那种感觉的，再放一次」</p>
      <p class="empty-shortcut">Ctrl+K 或 / 聚焦输入 · 空格播放/暂停</p>
    </div>

    <!-- 消息流 -->
    <div class="msg-list" v-else>
      <div
        v-for="msg in chat.messages"
        :key="msg.id"
        class="msg"
        :class="[`msg-${msg.role}`, { 'msg-streaming': msg.state === 'streaming', 'msg-error': msg.state === 'error' }]"
      >
        <div class="msg-text">
          <template v-if="msg.state === 'streaming' && !msg.text">
            <span class="typing-dots"><i></i><i></i><i></i></span>
          </template>
          <template v-else>
            {{ msg.text }}<span v-if="msg.state === 'streaming'" class="cursor"></span>
          </template>
        </div>

        <!-- 歌曲卡片 -->
        <div class="song-card" v-if="msg.song">
          <div class="song-card-label">NOW PLAYING</div>
          <div class="song-card-title">{{ msg.song.name }}</div>
          <div class="song-card-artist">{{ msg.song.artist }}</div>
          <div class="song-card-actions">
            <button
              class="song-dislike-btn"
              :class="{ marked: dislikedIds.has(msg.song.song_id) }"
              @click="onDislike(msg.song)"
            >
              {{ dislikedIds.has(msg.song.song_id) ? '✕ 已标记不喜欢' : '✕ 不喜欢这首' }}
            </button>
          </div>
        </div>

        <!-- 错误块 -->
        <div class="msg-error-text" v-if="msg.state === 'error'">
          ERROR: {{ msg.errorText }}
        </div>
      </div>
    </div>
  </main>
</template>