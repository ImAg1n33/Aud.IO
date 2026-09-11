// 播放模式与本地队列的纯逻辑 —— 不依赖 Pinia/DOM，Node 可直接测试。
//
// RFC-020 §2.5 模式语义：
//   dj           电台：歌曲播完 → 自动续播（Agent 按品味选下一首）
//   loop         单曲循环（audio.loop）
//   list-loop    列表循环（会话歌单顺序，本地直放）
//   list-shuffle 列表随机（洗牌队列，一轮内不重复）

export const MODES = ['dj', 'loop', 'list-loop', 'list-shuffle']

export const MODE_LABELS = {
  dj: 'DJ',
  loop: '单曲循环',
  'list-loop': '列表循环',
  'list-shuffle': '列表随机',
}

export const MODE_SHORT = {
  dj: 'DJ',
  loop: '1',
  'list-loop': 'LIST',
  'list-shuffle': 'SHUF',
}

export function nextMode(current) {
  const idx = MODES.indexOf(current)
  return MODES[(idx + 1) % MODES.length]
}

export function isListMode(mode) {
  return mode === 'list-loop' || mode === 'list-shuffle'
}

/** 会话歌单缓存（新→旧，来自 /history）→ 播放顺序（旧→新，最早播放在前）。 */
export function toPlayOrder(history) {
  return Array.isArray(history) ? [...history].reverse() : []
}

/** 列表循环：顺序下一首（到尾部回到头部）。currentId 不在队列 → 从头开始。 */
export function nextInList(order, currentId) {
  if (!Array.isArray(order) || order.length === 0) return null
  const idx = order.indexOf(currentId)
  if (idx === -1) return order[0]
  return order[(idx + 1) % order.length]
}

/**
 * Fisher-Yates 洗牌（注入随机源以便测试）。
 * 返回新数组，不修改输入。
 */
export function makeShuffle(ids, rand = Math.random) {
  const result = [...ids]
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[result[i], result[j]] = [result[j], result[i]]
  }
  return result
}

/**
 * 列表随机：洗牌顺序下一首（到尾部重新洗牌，避免同一列表内重复）。
 * 返回 { songId, shuffle }：shuffle 为（可能更新过的）洗牌数组。
 */
export function nextInShuffle(shuffle, currentId, rand = Math.random) {
  if (!Array.isArray(shuffle) || shuffle.length === 0) return null
  const idx = shuffle.indexOf(currentId)
  if (idx === -1) return { songId: shuffle[0], shuffle }
  if (idx + 1 >= shuffle.length) {
    // 一轮播完 → 重新洗牌从头开始
    const reshuffled = makeShuffle(shuffle, rand)
    return { songId: reshuffled[0], shuffle: reshuffled }
  }
  return { songId: shuffle[idx + 1], shuffle }
}
