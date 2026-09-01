<p align="center">
  <img src="./docs/screenshot.png" alt="Aud.IO UI Screenshot" width="100%">
</p>

# Aud.IO

<p align="center">
  <b>AI 音乐 DJ —— 懂你心情、记得你口味、会开口说话的智能电台</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Vue.js-3.x-4FC08D?style=flat-square&logo=vue.js&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Python-3.11_|_3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/ChromaDB-Vector_Memory-FF6F00?style=flat-square&logo=database&logoColor=white" alt="ChromaDB">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/CI-passing-brightgreen.svg?style=flat-square" alt="CI">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square" alt="PRs Welcome">
</p>

---

## ✨ 核心特性

<table>
  <tr>
    <td width="50%">
      <h3>🎧 专业 DJ 朋友</h3>
      <p>不是冷冰冰的播放器指令，也不是字正腔圆的电台播音。Aud.IO 像懂音乐的朋友一样和你对话——说一句"来首轻松的"，它理解你的语境，推荐一首真正合适的歌，用打字机效果说出来，然后自动播放。原生 function calling 让工具调用稳定可靠。</p>
    </td>
    <td width="50%">
      <h3>🧠 记忆系统：会学、会记、不失忆</h3>
      <p><b>会学</b>：完整听完 vs 中途切歌——你的真实听歌行为会校准记忆权重；<b>会记</b>：BGE 中文向量 + 关键词混合检索，加上 Ebbinghaus 衰减重排，说"上次那种感觉的"也能找到；<b>不失忆</b>：每 10 轮对话自动生成会话摘要，下次打开 DJ 还记得你上次的口味。</p>
    </td>
  </tr>
  <tr>
    <td>
      <h3>🎨 Nothing Design</h3>
      <p>受 Nothing 品牌美学启发的双模式界面——暗色模式的深邃哑光黑、亮色模式的通透冷白，三层视觉层级、无衬线字体、低饱和度色彩，让音乐成为唯一的焦点。</p>
    </td>
    <td>
      <h3>🎙️ 语音 DJ + 容错</h3>
      <p>基于 SSE 的逐字流式输出 + 可选 TTS 语音播报（通过 MCP 挂载外部服务）。音乐先响，语音不阻塞。Web Audio API 淡入淡出过渡，连接中断自动重试，版权歌曲自动换曲，ChromaDB 降级 SQLite。</p>
    </td>
  </tr>
</table>

---

## 🏗️ 全栈架构

```
用户输入 → 意图分类 (Hard Signal → LLM → 关键词) → 上下文组装 (7 个 Provider，含跨会话摘要)
    │                                                         │
    │  MUSIC_PLAY ──→ Phase 1 静默预取 → Phase 2 DJ 文案 + 音乐播放
    │  其他意图  ──→ Single-Pass 流式 → 原生 function calling → 工具执行 (版权重试)
    │                                                         │
    └──→ EpisodicMemory (Facade) ──→ SqliteRepository + ChromaRepository
          │  混合检索 (BGE 语义 + 关键词 RRF) → 衰减重排 → record_access()
          │  播放反馈 (finished/skipped) → importance_score 校准
          └──→ SessionReflector → 每 10 轮会话摘要 → 跨会话注入
```

| 层 | 技术选型 | 为什么 |
|----|----------|--------|
| 前端 | Vue 3 + Pinia + Vite 8 | SPA，4 组件拆分，Web Audio API，手写 CSS，SSE 状态机解析，语音播放队列 |
| 后端 | FastAPI + Uvicorn | 异步原生支持，SSE 流式零额外开销，全链路 session 隔离 |
| TTS | MCP → ToolRegistry → TTSProvider | 外部服务挂载，ToolRegistry 查找，音乐不等待语音 |
| LLM | DeepSeek（OpenAI 兼容协议） | 原生 function calling + 分层 Prompt；推理模型 thinking 显式禁用防 token 浪费 |
| 向量记忆 | ChromaDB + fastembed BGE-small-zh-v1.5 | 中文优化的 512 维本地模型（~95MB），对音乐/心情语义区分度显著优于 MiniLM（检索 MRR 提升近 2 倍） |
| SQL 记忆 | SQLite | 双写兼容，SQL 聚合统计，播放反馈信号（听完/切歌）校准记忆权重，语义检索降级回退 |
| 跨会话记忆 | SessionReflector + session_summaries 表 | 每 10 轮 LLM 摘要入库，新会话自动注入——DJ 跨会话不失忆 |
| 混合检索 | 语义 Top-K + SQLite LIKE → RRF 融合（k=60）→ 衰减重排 | 转述查询靠向量、原文/歌名/艺人子串靠关键词，两路互补 |
| 数据库迁移 | schema_version 表 + MigrationManager | 版本化、幂等（v1-v5），支持自愈修复历史库缺列 |
| 音乐服务 | NetEase Cloud Music API | 曲库覆盖广，Cookie 过期自动检测 + 瞬时错误重试 |
| 工具协议 | 原生 function calling + MCP | OpenAI 标准 tools 协议；MCP（stdio）接入外部工具生态 |
| 运行时数据 | `backend/data/`（`AUD_IO_DATA_DIR` 可配） | 与源码解耦，Docker volume 单目录映射，备份/迁移明确；内含双日志：`llm_calls.jsonl`（LLM 级调用）+ `conversations.jsonl`（会话级 trace，`scripts/view_conversations.py` 查看） |

---

## 🚀 一分钟跑起来

### 前置条件

- [Docker](https://docs.docker.com/get-started/) 已安装并运行
- DeepSeek API Key（[申请地址](https://platform.deepseek.com/)）

### 一键部署（Docker Compose）

```bash
git clone https://github.com/ImAg1n33/Aud.IO.git
cd Aud.IO

# 配置 API Key
cp backend/.env.example backend/.env
# 编辑 backend/.env 填入 LLM_API_KEY（必填）和 NETEASE_COOKIE（音乐播放需要）
# 推荐将 EMBEDDING_PROVIDER 设为 fastembed（BGE 中文向量，首次运行自动下载 ~95MB 模型）

# 一键启动（后端 + 前端 + 网易云音乐 API）
docker compose up -d

# 打开浏览器 → http://localhost
```

三服务自动编排：
- **前端** Nginx（:80）— SPA 静态托管 + `/api` 反向代理
- **后端** FastAPI（:8001）— 仅内网，通过 Nginx 代理访问
- **网易云 API** — 内网，供后端调用

### 本地开发（不依赖 Docker）

```bash
# 后端
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
uvicorn backend.main:app --reload --port 8001

# 前端（新终端）
cd frontend && npm install && npm run dev

# 打开 http://localhost:5173（Vite dev server 自带 /api 代理）
```

> 本地开发需 Node.js 20+ 和 Python 3.11+，并自行启动 NetEaseCloudMusicApi 实例。

---

## 📚 文档导航

| 文档 | 给谁看 | 内容 |
|------|--------|------|
| [docs/architecture.md](docs/architecture.md) | 想读懂代码的人 | 请求管线、组件地图、迁移历史、部署约束 |
| [docs/api.md](docs/api.md) | 对接/二开的人 | 端点用途、SSE 事件序列、反馈校准规则（字段细节见 `/docs` 自动 schema） |
| [docs/security-playbook.md](docs/security-playbook.md) | 部署者 | 密钥管理、泄露应急、提交防护 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 想参与的人 | 环境搭建、测试、**文档同步清单**、PR 约定 |
| `GET /docs` · `GET /redoc` | 所有人 | FastAPI 自动生成的完整 API Reference |

---

## 📂 项目导览

```
Aud.IO/
├── backend/
│   ├── main.py                         # FastAPI 入口 + MCP 生命周期
│   ├── .env / .env.example
│   ├── data/                            # 运行时数据 (episodes.db / chroma / profiles)
│   ├── data_config.py                   # 数据路径统一管理 (AUD_IO_DATA_DIR)
│   ├── api/
│   │   ├── routes_agent.py              # REST + SSE + /feedback 端点
│   │   └── _security.py                 # session_id 校验
│   ├── services/
│   │   ├── assistant_service.py        # ★ 核心编排 (Perceive→Decide→Execute→Record + Reflection 触发)
│   │   └── session_manager.py          # TTL 会话池
│   ├── agent/
│   │   ├── prompts.py                  # ★ 全部 Prompt 集中管理 (Layer 0-4 + 反射摘要)
│   │   ├── prompt_builder.py           # backward-compat re-exports
│   │   ├── intent_classifier.py        # 混合意图分类 (Hard Signal → LLM → 关键词)
│   │   ├── context_assembler.py        # ★ 插件式上下文组装 (7 Providers)
│   │   ├── llm_client.py               # httpx LLM 客户端 (stream + non-stream + 原生 function calling)
│   │   ├── tool_executor.py            # 工具调度 + 版权重试
│   │   └── memory_manager.py           # 用户画像 JSON Patch 更新
│   ├── memory/                         # ★ 仓储模式拆分 (RFC-008)
│   │   ├── episodic_memory.py          # Facade 编排 + 混合检索 + 反馈闭环
│   │   ├── _sqlite_repo.py             # SqliteRepository — 纯 SQLite CRUD + 摘要/反馈
│   │   ├── _chroma_repo.py             # ChromaRepository — 纯向量操作
│   │   ├── _migration.py               # MigrationManager — 版本化迁移 (v1-v5, 自愈)
│   │   ├── models.py                   # EpisodicSnapshot + 工具函数
│   │   ├── mood_detector.py            # MoodDetector — 中英文心情检测
│   │   ├── decay.py                    # 衰减公式 (Ebbinghaus 遗忘曲线)
│   │   ├── fusion.py                   # ★ RRF 混合检索融合
│   │   ├── reflection.py               # ★ SessionReflector — 会话摘要 (跨会话不失忆)
│   │   ├── conversation_memory.py      # 短时对话记忆
│   │   ├── embedding.py                # FastEmbed(BGE) / ONNX / API 向量嵌入
│   │   └── profile_schema.py           # Pydantic 画像验证
│   └── tools/                          # 工具层
│       ├── base.py                     # BaseTool + ToolRegistry + 意图门控 (category)
│       ├── music_tool.py               # 网易云搜索 + MP3 URL 获取
│       ├── netease_api.py              # 网易云 API + Cookie 过期 + 重试
│       ├── mcp_adapter.py              # MCP Client (RFC-001)
│       └── login_netease.py            # QR 码登录
├── frontend/
│   ├── Dockerfile                      # 多阶段构建 (Node 构建 + Nginx 托管)
│   ├── nginx.conf                      # Nginx (SPA + /api 反代 + SSE)
│   └── src/
│       ├── App.vue                     # 根布局 + 会话接线 + 键盘快捷键
│       ├── components/
│       │   ├── ChatPanel.vue           # 气泡消息流 + 歌曲卡片 + 空态引导
│       │   ├── PlayerPanel.vue         # 播放器 + 进度条/音量/真实控制 (NEXT/PREV/MODE)
│       │   ├── InputBar.vue            # 用户输入
│       │   └── DebugPanel.vue          # JSON 调试面板
│       ├── stores/
│       │   ├── chat.js                 # 消息流 store + SSE 处理 + 打字机引擎 (纯 reducer 可测)
│       │   ├── sse-parser.js           # SSE 状态机解析器 (跨 chunk 鲁棒)
│       │   ├── feedback.js             # ★ 播放反馈上报 (听完/切歌/失败)
│       │   └── player.js               # Web Audio API + 播放状态 + 控制
│       └── style.css                   # Nothing Design 暗/亮双模式
├── eval/                               # ★ 评估基线 (golden sets + runners)
│   ├── golden_intents.json             # 意图 Golden Set (60 用例)
│   ├── golden_retrieval.json           # 检索评估集 (12 seeds / 11 queries)
│   ├── run_intent_eval.py              # 意图评估 (hybrid/keyword/llm 三模式)
│   └── run_retrieval_eval.py           # 检索评估 (recall@k / MRR)
├── docker-compose.yml                  # 三服务编排
├── tests/                              # pytest (271 cases) + Node (15 cases)
├── docs/
│   ├── architecture.md / api.md / security-playbook.md
├── .github/workflows/ci.yml            # CI: Python 3.11+3.12, ruff, pytest, eval 报告, secret scan
├── LICENSE / CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md / CHANGELOG.md
└── scripts/
    ├── cleanup_profiles.py
    ├── rebuild_embeddings.py           # ★ 切换嵌入模型后重建向量索引 (维度自动检测)
    └── security_scan.py
```

---

## 🧪 质量保障

| 维度 | 实践 |
|------|------|
| 测试 | pytest + pytest-asyncio + Node test runner，271 pytest + 15 Node 用例覆盖全部模块 |
| 评估基线 | 意图 Golden Set（60 用例）+ 检索评估集（12 seeds / 11 queries），keyword 模式 85%，检索 recall@5 81.8% / MRR 0.644（BGE） |
| CI | GitHub Actions：Python 3.11 + 3.12 matrix，ruff lint 门禁，意图评估报告 artifact，secret scan |
| 安全 | Pre-commit Hook 拦截凭证提交，session_id 白名单校验，`.gitignore` 保护运行时数据与 `.env` |
| 容错 | LLM 流式中断优雅降级，版权歌曲自动换曲重试（最多 2 次），ChromaDB 降级到 SQLite，SSE 跨 chunk 鲁棒，嵌入 API 降级本地模型 |
| 代码质量 | ruff All checks passed，仓储模式 + Facade 封装，源码与运行时分离，`.env` 加载优先于服务初始化 |

---

## 🔒 数据出站透明（隐私）

Aud.IO 运行时的第三方出站调用（全部在**后端**发起，浏览器 IP 从不外发）：

| 目标 | 用途 | 数据 | 关闭方式 |
|------|------|------|----------|
| DeepSeek API | LLM 推理 | 对话文本/工具调用 | 不部署即无 |
| 网易云 API | 音乐搜索/播放 | 搜索词/歌曲 ID | 不配置 `NETEASE_COOKIE` |
| wttr.in | 天气上下文 | **服务器出口 IP**（用于城市定位）或查询城市 | 设置 `WEATHER_CITY` 后**不再发送 IP**（推荐） |
| HuggingFace | BGE 嵌入模型下载 | 无（仅首次下载模型文件） | 预置模型后离线 |

> 隐私要点：前端只与自建后端通信；天气使用 IP 定位时仅发送**服务器**出口 IP（城市级近似），
> 不涉及浏览器 IP、不存储位置；显式设置 `WEATHER_CITY` 可完全消除 IP 出站。

---

## 🤝 扩展 & 贡献

Aud.IO 的 Agent 管道采用**插件式架构**，扩展新能力非常自然：

- **接入新 LLM**：OpenAI 兼容协议可直接使用。`llm_client.py` 已内置提供者识别，选择 `LLM_PROVIDER=openai` 即可切换
- **接入新工具**：继承 `BaseTool`（设置 `category` 参与意图门控），注册到 `ToolRegistry` 后通过**原生 function calling** 自动暴露给 LLM；或通过 MCP 协议接入外部工具服务
- **接入新上下文**：实现 `ContextProvider` 接口，注入到 `ContextAssembler` 的 Provider 列表即可参与 Prompt 组装
- **切换嵌入模型**：`EMBEDDING_PROVIDER=fastembed|api|local` 三选一，切换后运行 `python scripts/rebuild_embeddings.py` 重建向量索引（维度自动检测）

```python
# 添加一个新的上下文来源 —— 就这么简单
class MyProvider(ContextProvider):
    name = "my_context"
    async def get_context(self, intent, user_input, metadata):
        return "[My Context]\n...有用的信息..."

assembler.providers.append(MyProvider())
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 📄 License

MIT © 2026 Aud.IO
