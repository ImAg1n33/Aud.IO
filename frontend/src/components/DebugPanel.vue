<script setup>
import { useChatStore } from '../stores/chat'

defineProps({
  visible: { type: Boolean, default: false },
})

const chat = useChatStore()
</script>

<template>
  <div class="debug-panel" v-show="visible">
    <div class="debug-label">RAW DATA</div>
    <pre>{{ chat.jsonDump }}</pre>
  </div>
</template>
