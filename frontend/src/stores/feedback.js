// 播放反馈上报 —— 把用户真实的听歌行为（听完/切歌/失败）回传给后端，
// 让 DJ 从结果中学习（importance_score 校准）。
//
// 设计原则：
// - fire-and-forget：失败静默，绝不阻塞或打断播放
// - keepalive：页面关闭瞬间的反馈也能送达
// - session_id 从 localStorage 读取（与 App.vue 会话生成逻辑同源）

export function sendFeedback(event, track, listenSeconds = null) {
  if (!track?.song_id) return Promise.resolve(false)
  if (!event) return Promise.resolve(false)

  const sessionId = localStorage.getItem('aud_io_session') || undefined

  const body = {
    event,
    song_id: track.song_id,
    listen_seconds: listenSeconds,
    session_id: sessionId,
  }

  return fetch('/api/v1/agent/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    keepalive: true,
    body: JSON.stringify(body),
  })
    .then((res) => res.ok)
    .catch(() => false)
}