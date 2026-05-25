<script setup>
import { useChatStore } from '../stores/chat'

const chat = useChatStore()
</script>

<template>
  <main class="response-area">
    <p class="response-text" :style="{ color: chat.responseColor }">
      {{ chat.responseText }}<span class="cursor"></span>
    </p>
  </main>
</template>
