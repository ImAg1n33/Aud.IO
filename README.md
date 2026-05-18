# Aud.IO

AI 音乐 DJ 助手 —— FastAPI 后端 + Vue 3 前端，具备流式 LLM 对话、语义情节记忆、网易云音乐播放和 Nothing Design 风格界面。

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 前端 | Vue 3 (Composition API) + Vite 8 | Nothing Design 风格，打字机流式 UI + Web Audio 播放器 |
| 后端 | FastAPI + Uvicorn | 异步 SSE 流式响应，Perceive→Decide→Execute→Record 编排管道 |
| LLM | DeepSeek API (OpenAI 兼容协议) | 流式逐字输出，---JSON--- 标记分隔结构 |
| 向量记忆 | ChromaDB + ONNX all-MiniLM-L6-v2 | 本地离线向量嵌入，语义检索替代关键词 mood 映射 |
| 情节记忆 | SQLite (双写兼容) | ChromaDB 写入同步双写到 SQLite，支持 SQL 聚合统计 |
| 用户画像 | Pydantic + JSON Patch | LLM 驱动的异步画像更新，原子写入防损坏 |
| 音乐服务 | NetEase Cloud Music Unblocked API | 第三方网易云 API，搜索 + MP3 URL 获取 + 版权重试 |
| 测试 | pytest + pytest-asyncio | 114 个测试用例，覆盖全部核心模块 |

## 项目结构

```
Aud.IO/
├── backend/
│   ├── main.py                         # FastAPI 应用入口 + CORS
│   ├── api/routes_agent.py             # POST /v1/agent/respond + /respond/stream (SSE)
│   ├── services/assistant_service.py   # ★ 核心编排：Perceive→Decide→Execute→Record
│   ├── agent/
│   │   ├── llm_client.py              # LLM 调用 + 流式重试（连接级/推流级区分）
│   │   ├── prompt_builder.py          # 系统提示词模板
│   │   ├── context_assembler.py       # ★ 插件式上下文组装（5 个 Provider）
│   │   ├── intent_classifier.py       # 规则引擎意图分类（零 LLM 成本）
│   │   ├── tool_executor.py           # 工具调度（重试 + 版权兜底）
│   │   └── memory_manager.py          # JSON Patch 驱动用户画像异步更新
│   ├── memory/
│   │   ├── episodic_memory.py         # ★ ChromaDB + SQLite 双写情节记忆 + MoodDetector
│   │   ├── embedding.py               # 向量嵌入抽象层（本地 ONNX / 远端 API）
│   │   ├── conversation_memory.py     # 短期对话记忆（内存环形缓冲，max 20 turns）
│   │   └── profile_schema.py          # Pydantic 用户画像模型 + 原子写入
│   └── tools/
│       ├── base.py                    # 工具抽象层（BaseTool, ToolRegistry）
│       ├── music_tool.py              # 网易云音乐搜索 + MP3 URL 获取
│       ├── netease_api.py             # 网易云 Unblocked API 低层封装
│       ├── login_netease.py           # 网易云扫码登录脚本
│       ├── weather.py                 # 天气工具（桩）
│       └── tts.py                     # TTS 工具（桩）
├── frontend/
│   ├── vite.config.js                 # Vite 构建配置 + /api 代理
│   └── src/
│       ├── App.vue                    # ★ 全部 UI：终端风输入 + SSE 流式 + 音频播放器
│       └── style.css                  # Nothing Design 暗/亮双模式样式
├── tests/                             # pytest 测试套件（114 cases）
├── docs/                              # 架构文档 + API 文档 + 安全手册
├── scripts/security_scan.py           # API Key 泄露扫描
└── .github/workflows/ci.yml           # CI：pytest + 安全扫描
```

## 快速开始

1. 创建 Python 虚拟环境并激活
2. 安装后端依赖：
   ```
   pip install -r backend/requirements.txt
   ```
3. 配置环境变量：
   ```
   copy backend/.env.example backend/.env
   ```
   编辑 `.env` 填入 LLM API Key（必填）和网易云 Cookie（音乐播放必填）
4. 启动后端：
   ```
   uvicorn backend.main:app --reload --port 8001
   ```
5. 启动前端（新终端）：
   ```
   cd frontend && npm install && npm run dev
   ```
6. 打开浏览器访问 `http://localhost:5173`

## 记忆系统架构 (v2.0)

| 层级 | 组件 | 存储 | 说明 |
|------|------|------|------|
| 短期记忆 | ConversationMemory | 内存环形缓冲 (max 20) | 当前会话对话历史 |
| 情节记忆 (主) | EpisodicMemory → ChromaDB | `chroma_episodes/` | 向量语义检索，自动 mood 检测 |
| 情节记忆 (兼容) | EpisodicMemory → SQLite | `episodes.db` | 双写兼容，SQL 聚合统计 |
| 用户画像 | MemoryManager → JSON Patch | `user_profile.json` | LLM 驱动的异步偏好更新 |

**语义检索工作流：**
1. 用户输入 → MoodDetector 中英文关键词检测（50+ 词表，<1ms） → 自动打 mood_tag
2. 用户输入 → 本地 ONNX 模型向量化（384 维） → ChromaDB 余弦相似度检索
3. 检索结果 → EpisodicMemoryProvider 注入 LLM 上下文，增强推荐个性化

**ChromaDB 模式：**
- 默认离线：`all-MiniLM-L6-v2` ONNX 模型（~80MB，首次自动下载）
- 可选远端：设置 `EMBEDDING_PROVIDER=api`，走 OpenAI 兼容 `/embeddings` 端点

## API 路由

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/ready` | 就绪检查 |
| POST | `/v1/agent/respond` | 标准响应（非流式，JSON） |
| POST | `/v1/agent/respond/stream` | **SSE 流式响应**（打字机效果 + 音乐播放） |

### SSE 事件类型

```
event: token  → 逐字打字机流（28ms/字 速度控制）
event: text   → 完整回复文本（替换显示）
event: music  → JSON 音乐对象（触发播放 + Web Audio 淡入淡出）
event: error  → 错误消息（区分连接失败 / 流式中断）
event: done   → JSON 完整响应（debug 面板）
```

## 测试

```
pip install -r requirements-dev.txt
pytest                                  # 114 个测试用例
```

## CI

GitHub Actions（`.github/workflows/ci.yml`）：push 到 main 或 PR 时触发 pytest + 安全扫描。

## LLM 配置

在 `.env` 中通过通用环境变量适配不同 LLM 提供商：

```
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=your_key_here
```

支持的提供商：DeepSeek、OpenAI、Anthropic（需实现对应的 LLM Provider 适配层）。

## 安全

- **绝不提交** API Key、`.env`、`user_profile.json`、`episodes.db`、`chroma_episodes/`、`memory_update.log`
- 只提交 `.env.example` 作为模板
- Pre-commit hook（`.githooks/pre-commit`）拦截敏感文件
- `scripts/security_scan.py` 可手动扫描
- 详见 `docs/security-playbook.md`
