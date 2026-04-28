<script setup>
import { ref } from 'vue'

// Vue 的魔法：响应式变量
const userInput = ref('')
const responseText = ref('> SYSTEM READY. AWAITING COMMAND.')
const responseColor = ref('#ffffff')
const isProcessing = ref(false)
const showPlayer = ref(false)
const showDebug = ref(false)
const trackName = ref('///')
const jsonDump = ref('')
const audioElement = ref(null)

let currentPlayingTrack = "None"

// --- 新增：播放器控制状态与方法 ---
const isPlaying = ref(false)
const playMode = ref('dj') // 默认 DJ 模式

const togglePlay = () => {
  if (!audioElement.value) return
  if (isPlaying.value) {
    audioElement.value.pause()
  } else {
    audioElement.value.play()
  }
  isPlaying.value = !isPlaying.value
}

const toggleMode = () => {
  // 简单的模式循环: dj -> loop -> list
  const modes = ['dj', 'loop', 'list']
  const currentIndex = modes.indexOf(playMode.value)
  playMode.value = modes[(currentIndex + 1) % modes.length]
}

const prevTrack = () => {
  console.log("点击了上一首，待接入 Pinia")
}

const nextTrack = () => {
  console.log("点击了下一首，待接入 Pinia")
}

const onAudioEnded = () => {
  isPlaying.value = false
  if (playMode.value === 'dj') {
    // 💡 这里的逻辑极其关键：播完自动让 AI 说话！
    console.log("DJ 模式：歌曲结束，准备呼叫大模型...")
  }
}

// 发送指令的核心函数
const sendCommand = async () => {
  const text = userInput.value.trim()
  if (!text) return

  // 1. 改变 UI 状态为“思考中”
  userInput.value = ''
  isProcessing.value = true
  responseColor.value = '#888'
  responseText.value = '> TRANSMITTING DATA TO LLM CORE...\n> SEARCHING DATABASE...'
  showPlayer.value = false
  if (audioElement.value) audioElement.value.pause()

  try {
    // 2. 发送请求给后端
    const response = await fetch("http://127.0.0.1:8001/v1/agent/respond", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_input: text,
        context: { "Currently Playing": currentPlayingTrack }
      })
    })

    if (!response.ok) throw new Error("SERVER REJECTED CONNECTION.")
    
    const data = await response.json()
    const reply = data.reply

    // 3. 更新神经监控面板
    jsonDump.value = JSON.stringify(data, null, 2)
    showDebug.value = true

    // 4. 更新大模型回复
    responseColor.value = '#ffffff'
    responseText.value = "> " + (reply.answer || reply.say || "TASK COMPLETED.")

    // 5. 播放音乐
    if (reply.music && reply.music.mp3_url) {
      audioElement.value.src = reply.music.mp3_url
      audioElement.value.play()
      isPlaying.value = true
      currentPlayingTrack = `${reply.music.artist} - ${reply.music.name}`
      trackName.value = `♪ ${reply.music.name} - ${reply.music.artist} ♪`
      showPlayer.value = true
    }

  } catch (error) {
    responseColor.value = 'var(--accent-red)'
    responseText.value = "> CRITICAL ERROR: " + error.message
  } finally {
    isProcessing.value = false
  }
}
</script>

<template>
  <div class="container">
    <div class="header">Aud.IO</div>

    <div class="display-box">
      <span :style="{ color: responseColor }" style="white-space: pre-wrap;">{{ responseText }}</span>
      <span class="cursor"></span>
    </div>

    <div class="input-group">
      <input 
        type="text" 
        v-model="userInput" 
        @keypress.enter="sendCommand"
        placeholder="TYPE YOUR REQUEST..." 
        autocomplete="off"
      >
      <button :disabled="isProcessing" @click="sendCommand">
        {{ isProcessing ? 'PROCESSING' : 'EXECUTE' }}
      </button>
    </div>

    <div class="player-panel" v-show="showPlayer">
      <div style="font-size: 1.2rem; color: #888; margin-bottom: 5px;">NOW PLAYING:</div>
      <div class="track-info">{{ trackName }}</div>
      
      <div class="control-board">
        <button class="ctrl-btn mode-btn" @click="toggleMode" title="切换模式">
          MODE: [{{ playMode.toUpperCase() }}]
        </button>
        <button class="ctrl-btn" @click="prevTrack" title="上一首">|◀◀</button>
        <button class="ctrl-btn play-btn" @click="togglePlay">
          {{ isPlaying ? '■ PAUSE' : '▶ PLAY' }}
        </button>
        <button class="ctrl-btn" @click="nextTrack" title="下一首">▶▶|</button>
      </div>

      <audio ref="audioElement" @ended="onAudioEnded"></audio>
    </div>

    <div class="debug-panel" v-show="showDebug">
      <div style="font-size: 0.9rem; margin-bottom: 10px; border-bottom: 1px solid var(--accent-red); padding-bottom: 5px;">[ RAW DATA DUMP ]</div>
      <pre>{{ jsonDump }}</pre>
    </div>
  </div>
</template>