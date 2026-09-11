# Changelog

All notable changes to Aud.IO are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **播放模式四分（RFC-020）**：DJ 电台 / 单曲循环 / 列表循环 / 列表随机 ——
  `queue.js` 纯逻辑 + 模式切换器；列表模式本地直放（无 LLM 等待），缓存不足
  优雅回退 Agent
- **DJ 电台自动续播**：歌曲自然播完 → 自动选下一首（相似歌曲优先，
  LLM 按画像兜底），排除本会话最近 10 首防重复；不伪造用户气泡（AUTO 标记）；
  失败 4s 重试、连续 3 次熔断；trace `path=auto_dj`
- **播放历史（迁移 v7）**：`play_history` 表 —— 每次 music 发出即写入 +
  会话内去重；会话缓存 50 / 跨会话最近 100 自动淘汰；mp3 URL 永不缓存
- **历史与回放 API**：`GET /v1/agent/history?scope=session|global` +
  `POST /v1/agent/replay`（重解析 mp3_url，兼作列表模式播放通道）
- **歌曲元数据**：`music` 载荷新增 `cover_url` / `album` / `duration_ms`
  （cloudsearch 透传），前端封面图 + 时长即刻可用
- **前端 v0.8 体验重设计**：常驻 Now Playing（standby 态等价，零布局跳变）、
  历史抽屉（本会话/最近，点播回放）、结构化搜索状态编排、频谱可视化
  （AnalyserNode）、字体自托管（Doto/Space Grotesk/Space Mono woff2，
  运行时零外网请求）、主题防闪（内联脚本）、focus-visible / aria /
  prefers-reduced-motion、空态示例一键发送、消息流自动跟随（上滚不打扰）

### Changed
- **DJ 语气专业化（prompt 调优）**：实测反馈"像朋友但不够专业"——自动续播会
  罗列已播歌曲（"A、B、C 都放完了"），且高频套话（"钢琴一进来就是那种…"、
  "整个人都…"、"心就软了"）。Prompt 强化：禁止回放清单/过渡播报、扩充
  AI 腔禁词表（整个人都/心都软了/一进来/绝了…）、要求以具体音乐信息
  （乐器编制/制作/年代/词曲作者）替代情绪化比喻（实测：罗列消失，
  输出转为词作/编曲细节）

### Fixed
- **AUTO 续播覆盖上一条消息**：`autoContinue` 的消息复用条件写成"非 streaming"
  （成功消息也被复用）→ 第三首会把第二首的歌曲卡片覆盖掉。收紧为
  **仅复用失败（error）的自动消息**做重试；成功消息永不复用（纯函数
  `shouldReuseAutoMessage` + Node 测试回归）
- **重复问候**：EnvironmentProvider 每次请求都注入"Use the time of day in your
  greeting" + Phase 2 结构把"time"列为第一锚点 → "换一首"等后续回复每条都以
  "早上好"开头。改为：时间问候仅限新会话首条；Phase 2 锚点优先用户话语/心情；
  Single-Pass 输出规则同步禁止会话中途重复问候（实测 1 问候 + 3 换歌无复读）
- **本地即时切歌被延迟暂停**：`stopTrack()` 的 1.3s 淡出定时器未在
  `playTrack()` 清除，列表模式本地直放会被旧定时器暂停 → 播放时清理
## [0.7.0] — 2026-09-10

### Added
- **挑歌精度（v0.7）**：
  - 搜索质量评分：拉取候选集（cloudsearch 优先）→ 确定性评分选优
    （`originCoverType` 原唱/翻唱信号 + 劣质标记惩罚 + 艺人匹配 + 热度），
    全为翻唱时抛 `NoAcceptableSongError`（可重试换曲）——不再静默播翻唱
    （实证：`周杰伦 晴天` 首位曾是 RyaVocal 翻唱）
  - `find_similar` 工具：基于 `/simi/song` 真实音乐关系数据（"来首类似的"/
    "来点不一样的" mode=explore），非 LLM 猜测
  - 口味硬约束：播放前校验 disliked 艺人 → 带反馈重试换艺人，仍命中则不播
  - "上一首"：从播放历史（episodes song_id 维度）回放上一条；前端 PREV 走指令
- **拒绝学习（显式 dislike）**：`/feedback` 新增 `song_disliked` 事件 —— 强降权
  （importance −0.3，区别于 skip 的 −0.15）+ `dislike_count`（migration v6）+
  确定性写入画像 `artist_preference.disliked`（跳过 LLM 解读，附审计
  `source: explicit_dislike`）；前端歌曲卡片新增"不喜欢"按钮
- **画像跨会话整合**：Memory Observer 输入带上最近 Reflection 摘要
  （防重复添加/防单轮表述稀释），Prompt 增加去重与新旧信号冲突规则

### Fixed
- **快照行映射错位**：`_row_to_snapshot` 原来按列位置读取，生产库因历史迁移
  顺序异常（`last_accessed` 晚补）使 `song_id` 实际位于第 13 列 → 读成
  `played_to_completion`(0)。改为 `sqlite3.Row` 按列名读取（tuple 兼容回退）
- **fastembed 缓存易失**：模型默认缓存 `/tmp/fastembed_cache` 不在挂载卷内，
  容器重建即丢、断网时嵌入停摆（静默降级）。改为显式 `cache_dir`
  （默认 `~/.cache/fastembed`，容器内即挂载卷路径），已预热持久化
- **Docker 构建静默失败**：`backend/.dockerignore` 缺失 `data/` 与
  `.chroma_cache/`（含 HF 符号链接的 270MB 模型缓存被传入构建上下文导致
  BuildKit 传输失败）；补齐忽略项

## [0.5.0] — 2026-09-01

### Added
- **会话级 trace 日志（Agent Observability）**：每次交互写入 `data/conversations.jsonl`
  （意图/路径/工具调用/音乐/回答/延迟/错误），`scripts/view_conversations.py` 人性化查看——
  与 `llm_calls.jsonl`（LLM 级）构成双层行为留存，便于复盘与调试
- **Reflection 会话摘要（v5）** — 跨会话连续性：
  - 每 10 轮对话自动触发 LLM 摘要（`SUMMARY_REFLECTION_SYSTEM`），产出
    {summary, topics, song_signals} 结构化入库（`session_summaries` 表）
  - `SessionSummaryProvider` 把历史会话摘要注入上下文——DJ 重启/换会话不失忆
  - 触发节流（`ctx.last_summary_turn`）、失败静默降级，不阻断对话
- Hybrid retrieval in `query_by_semantic`: semantic (ChromaDB) + keyword (SQLite LIKE on
  input/reply/song name/artist) fused via RRF (k=60), then decay rerank; ChromaDB-down
  still falls back to pure keyword
- `FastEmbedProvider` (fastembed): local Chinese-optimized BGE embeddings
  (`EMBEDDING_PROVIDER=fastembed`, `BAAI/bge-small-zh-v1.5` default)
- `scripts/rebuild_embeddings.py`: rebuild ChromaDB collection when switching embedding
  models (dimension detection + auto delete/recreate, batch upsert)
- Playback feedback loop: `POST /v1/agent/feedback` receives song_started/finished/skipped/failed events
- Frontend playback telemetry: `PlayerPanel` reports finish/skip/fail to calibrate memory importance
- Memory calibration: `record_play_feedback()` adjusts `importance_score` (+0.15 finished / -0.15 skipped, clamped 0.05-0.98)
- Migration v3: `song_id` + feedback columns (`played_to_completion`, `listen_duration`, `play_count`, `skip_count`, `last_feedback`)
- Eval baseline: intent golden set (60 cases) + retrieval eval set (12 seeds / 11 queries) with runners (`eval/`)
- Centralized prompt registry (`prompts.py`) with layered architecture (RFC-007)

### Changed
- **前端界面重设计（v0.6 体验）**：
  - 对话区升级为**气泡消息流**：用户/DJ 消息历史保留可回看，打字机只作用于最新消息
  - PREV/NEXT 实装：NEXT=快捷指令"换一首"（走 agent 通道并触发切歌反馈），PREV=重播当前曲
  - MODE 实装：dj / loop（`audio.loop` 循环）/ list；音量滑条（本地记忆）+ 进度条点击跳转
  - 播放状态可视化：缓冲指示点、加载态、错误块
  - 空态引导 + 键盘快捷键（Ctrl+K 或 / 聚焦输入、空格播放/暂停）
  - 会话标识内部化（chat store），移除 props 透传链
  - chat.js 消息流逻辑抽为纯 reducer（Node 可直接测试，+8 用例）
- **RFC: function calling 重构** — 移除 `---JSON---` 文本标记协议（`llm_client` / prompts）：
  - `call_llm` / `stream_llm` 支持 OpenAI 原生 `tools` 参数，tool_calls 归一化为
    `{"tool": name, ...args}` 动作 dict（流式分片自动拼接 arguments）
  - `_parse_actions_from_reply` 删除 json.loads/ast 兜底解析链（仅保留类型过滤）
  - `BaseTool.category` 意图门控：MUSIC_PLAY/RECOMMEND → 音乐工具；CHITCHAT/WEATHER → 无工具；
    UNKNOWN → 全部可用工具
  - 流式输出为纯文案（前端打字机直出），工具调用走标准协议，不再与文本混编
- Professional DJ + friend persona with forbidden broadcast phrases
- Hard play signal detection in intent classifier (eliminates song/emotion ambiguity)
- System prompts sent as `role="system"` for higher instruction adherence
- `not_found` status phase handling in frontend chat store

### Changed
- `call_llm()` and `stream_llm()` now accept `system_prompt` + `user_prompt` as separate args
- `ContextAssembler` no longer embeds system persona — only assembles user-prompt portion
- `prompt_builder.py` re-exports from centralized `prompts.py`
- Phase 1 decision no longer hallucinates artist names for bare song title queries

### Fixed
- **deepseek-v4-flash 空回答**: 该模型是推理模型，`reasoning_content` 会吃光 `max_tokens`
  预算导致 `content` 为空。新增 `_provider_extra_body()` 对 deepseek 显式
  `thinking: {"type": "disabled"}`（`LLM_DISABLE_THINKING=false` 可关），
  意图分类/流式回答/JSON 调用全部修复
- PHASE2_STREAM_SYSTEM missing JSON format example causing empty DJ scripts
- f-string brace escaping in Phase 2 prompt preventing module import on CI

## [0.3.1] — 2026-06-12

### Fixed
- `.env` loading order: `load_dotenv()` now runs before all backend imports, ensuring `AUD_IO_DATA_DIR`, `EMBEDDING_PROVIDER`, `MEMORY_MODEL` etc. take effect during service initialization
- Illegal `session_id` now returns HTTP 400 instead of 500

### Added
- `normalize_session_id()` input validation (accepts UUID / safe slug, rejects path traversal)
- `SSEParser` state machine for cross-chunk SSE event resilience
- `/ready` endpoint expanded with LLM, Embedding, ChromaDB, NetEase, MCP status fields

### Changed
- Runtime data directory moved from `backend/memory/` to `backend/data/` (`AUD_IO_DATA_DIR` env var)
- `ContextAssembler.assemble()` passes `session_id` to all memory providers (session-scoped recall)
- Version string corrected: `0.2.0` → `0.3.1`
- Removed stale `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` references (Anthropic Messages API not supported)

### Security
- `session_id` validated at API boundary against path traversal
- Runtime data (episodes, profiles, chroma) isolated from source tree in `backend/data/`

## [0.2.1] — 2026-05-24

- Architecture roadmap published

## [0.2.0] — 2026-05-20

### Added
- MCP protocol adapter layer (RFC-001)
- Two-Pass streaming architecture for music intents (RFC-003)
- Hybrid intent classifier: LLM primary + keyword fallback (RFC-004)
- Multi-user session isolation
- NetEase cookie expiry detection with auto-retry
- Unified HTTP client migration to httpx
- Environment context provider (weather + time of day) (RFC-005)
- Episodic memory vector search (ChromaDB semantic retrieval)
- User profile mood validation via Pydantic

### Changed
- App.vue split into ChatPanel, PlayerPanel, InputBar, DebugPanel
- Pinia stores activated for chat and player state
- `.env` loading unified to single entry point

### Removed
- Legacy `build_prompt()` function
- `frontend_backup/` directory
- All `urllib` dependencies

## [0.1.0] — 2026-05-11

### Added
- Initial architecture: FastAPI backend + Vue 3 frontend + Docker Compose
- ChromaDB + SQLite dual-write episodic memory
- SSE streaming with typewriter effect
- NetEase Cloud Music API integration
- Keyword-based intent classification
- Dark/light theme (Nothing Design)
