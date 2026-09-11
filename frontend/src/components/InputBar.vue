<script setup>
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

// IME 组合输入中不触发发送（中文输入法回车确认候选词）
function onEnter(e) {
  if (e.isComposing) return
  chat.sendCommand()
}
</script>

<template>
  <footer class="input-area">
    <div class="input-group">
      <input
        type="text"
        v-model="chat.userInput"
        @keydown.enter="onEnter"
        placeholder="说点什么，DJ 在听…"
        autocomplete="off"
        aria-label="输入消息"
      />
      <button
        class="send-btn"
        :disabled="chat.isProcessing"
        :aria-busy="chat.isProcessing"
        @click="chat.sendCommand()"
      >
        {{ chat.isProcessing ? 'WAIT' : 'SEND' }}
      </button>
    </div>
  </footer>
</template>
