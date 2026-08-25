<script setup>
import { useChatStore } from '../stores/chat'

const chat = useChatStore()
</script>

<template>
  <footer class="input-area">
    <div class="input-group">
      <input
        type="text"
        v-model="chat.userInput"
        @keypress.enter="chat.sendCommand()"
        placeholder="说点什么，DJ 在听…"
        autocomplete="off"
      />
      <button
        class="send-btn"
        :disabled="chat.isProcessing"
        @click="chat.sendCommand()"
      >
        {{ chat.isProcessing ? '...' : 'SEND' }}
      </button>
    </div>
  </footer>
</template>