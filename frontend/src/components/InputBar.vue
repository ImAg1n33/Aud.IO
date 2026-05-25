<script setup>
import { useChatStore } from '../stores/chat'

const props = defineProps({
  sessionId: { type: String, required: true },
  currentPlayingTrack: { type: String, default: 'None' },
})

const chat = useChatStore()
</script>

<template>
  <footer class="input-area">
    <div class="input-group">
      <input
        type="text"
        v-model="chat.userInput"
        @keypress.enter="chat.sendCommand(props.sessionId, props.currentPlayingTrack)"
        placeholder="What do you want to hear?"
        autocomplete="off"
      />
      <button
        class="send-btn"
        :disabled="chat.isProcessing"
        @click="chat.sendCommand(props.sessionId, props.currentPlayingTrack)"
      >
        {{ chat.isProcessing ? '...' : 'SEND' }}
      </button>
    </div>
  </footer>
</template>
