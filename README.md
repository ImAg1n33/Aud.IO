<p align="center">
  <img src="./docs/screenshot.png" alt="Aud.IO UI Screenshot" width="100%">
</p>

# Aud.IO

<p align="center">
  <b>AI 音乐 DJ —— 懂你心情、记得你口味、陪你聊天的智能电台</b>
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
      <p>不是冷冰冰的播放器指令，也不是字正腔圆的电台播音。Aud.IO 像懂音乐的朋友一样和你对话——说一句"来首轻松的"，它理解你的语境，推荐一首真正合适的歌，用打字机效果说出来，然后自动播放。</p>
    </td>
    <td width="50%">
      <h3>🧠 语义记忆 + 衰减</h3>
      <p>记住你喜欢过什么、什么心情下听过什么、常在什么时候打开它。基于 Ebbinghaus 遗忘曲线的加权重排——最近的、重要的、经常回想的记忆自动排在前面。即使你说"上次那种感觉的"，也能从向量记忆库中找到。</p>
    </td>
  </tr>
  <tr>
    <td>
      <h3>🎨 Nothing Design</h3>
      <p>受 Nothing 品牌美学启发的双模式界面——暗色模式的深邃哑光黑、亮色模式的通透冷白，三层视觉层级、无衬线字体、低饱和度色彩，让音乐成为唯一的焦点。</p>
    </td>
    <td>
      <h3>⚡ 流式响应 + 容错</h3>
      <p>基于 SSE 的逐字流式输出，配合 Web Audio API 的淡入淡出过渡。连接中断自动重试，版权歌曲自动换曲，ChromaDB 不可用时自动降级到 SQLite。</p>
    </td>
  </tr>
</table>

---

## 🏗️ 全栈架构

```
用户输入 → 意图分类 (Hard Signal → LLM → 关键词) → 上下文组装 (6 个 Provider)
    │                                                         │
    │  MUSIC_PLAY ──→ Phase 1 静默预取 → Phase 2 DJ 文案 + 音乐播放
    │  其他意图  ──→ Single-Pass 流式 → 工具执行 (版权重试)
    │                                                         │
    └──→ EpisodicMemory (Facade) ──→ SqliteRepository + ChromaRepository
                  │
                  └──→ 衰减加权重排 → record_access()
```

| 层 | 技术选型 | 为什么 |
|----|----------|--------|
| 前端 | Vue 3 + Pinia + Vite 8 | SPA，4 组件拆分，Web Audio API，手写 CSS |
| 后端 | FastAPI + Uvicorn | 异步原生支持，SSE 流式零额外开销 |
| LLM | DeepSeek（OpenAI 兼容协议） | 5 条调用路径，System Role 规范，分层 Prompt |
| 向量记忆 | ChromaDB + ONNX all-MiniLM-L6-v2 | 完全本地离线，~80MB 模型，首次自动下载 |
| SQL 记忆 | SQLite | 双写兼容，SQL 聚合统计，语义检索降级回退 |
| 数据库迁移 | schema_version 表 + MigrationManager | 版本化、幂等，支持增量 DDL + ChromaDB 回填 |
| 音乐服务 | NetEase Cloud Music API | 曲库覆盖广，Cookie 过期自动检测 + 瞬时错误重试 |
| 工具协议 | MCP (Model Context Protocol) | JSON-RPC stdio transport，支持外部工具发现 |

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

## 📂 项目导览

```
Aud.IO/
├── backend/
│   ├── main.py                         # FastAPI 入口 + MCP 生命周期
│   ├── .env / .env.example
│   ├── api/routes_agent.py             # REST + SSE 端点
│   ├── services/
│   │   ├── assistant_service.py        # ★ 核心编排 (Perceive→Decide→Execute→Record)
│   │   └── session_manager.py          # TTL 会话池
│   ├── agent/
│   │   ├── prompts.py                  # ★ 全部 Prompt 集中管理 (Layer 0-4)
│   │   ├── prompt_builder.py           # backward-compat re-exports
│   │   ├── intent_classifier.py        # 混合意图分类 (Hard Signal → LLM → 关键词)
│   │   ├── context_assembler.py        # ★ 插件式上下文组装 (6 Providers)
│   │   ├── llm_client.py               # httpx LLM 客户端 (stream + non-stream)
│   │   ├── tool_executor.py            # 工具调度 + 版权重试
│   │   └── memory_manager.py           # 用户画像 JSON Patch 更新
│   ├── memory/                         # ★ 仓储模式拆分 (RFC-008)
│   │   ├── episodic_memory.py          # Facade 编排 (409 行)
│   │   ├── _sqlite_repo.py             # SqliteRepository — 纯 SQLite CRUD
│   │   ├── _chroma_repo.py             # ChromaRepository — 纯向量操作
│   │   ├── _migration.py               # MigrationManager — 版本化迁移
│   │   ├── models.py                   # EpisodicSnapshot + 工具函数
│   │   ├── mood_detector.py            # MoodDetector — 中英文心情检测
│   │   ├── decay.py                    # 衰减公式 (Ebbinghaus 遗忘曲线)
│   │   ├── conversation_memory.py      # 短时对话记忆
│   │   ├── embedding.py                # ONNX + API 向量嵌入
│   │   └── profile_schema.py           # Pydantic 画像验证
│   └── tools/                          # 工具层
│       ├── base.py                     # BaseTool + ToolRegistry + 错误层级
│       ├── music_tool.py               # 网易云搜索 + MP3 URL 获取
│       ├── netease_api.py              # 网易云 API + Cookie 过期 + 重试
│       ├── mcp_adapter.py              # MCP Client (RFC-001)
│       └── login_netease.py            # QR 码登录
├── frontend/
│   ├── Dockerfile                      # 多阶段构建 (Node 构建 + Nginx 托管)
│   ├── nginx.conf                      # Nginx (SPA + /api 反代 + SSE)
│   └── src/
│       ├── App.vue                     # 根布局 (83 行, 4 子组件)
│       ├── components/
│       │   ├── ChatPanel.vue           # SSE 流 + 打字机效果
│       │   ├── PlayerPanel.vue         # Web Audio 播放器 + 淡入淡出
│       │   ├── InputBar.vue            # 用户输入
│       │   └── DebugPanel.vue          # JSON 调试面板
│       ├── stores/
│       │   ├── chat.js                 # SSE 解析 + 打字机引擎
│       │   └── player.js               # Web Audio API + 播放状态
│       └── style.css                   # Nothing Design 暗/亮双模式
├── docker-compose.yml                  # 三服务编排
├── tests/                              # pytest (155 cases, 12 files)
├── docs/
│   ├── architecture-reports/
│   │   ├── versions/                   # 架构版本快照 (v0.1→v0.3)
│   │   └── rfcs/                       # 设计提案 (RFC-001→008)
│   ├── architecture.md / api.md / security-playbook.md
├── .github/workflows/ci.yml            # CI: Python 3.11+3.12, ruff, pytest, secret scan
├── LICENSE / CONTRIBUTING.md / CODE_OF_CONDUCT.md / SECURITY.md / CHANGELOG.md
└── scripts/
    ├── cleanup_profiles.py
    └── security_scan.py
```

---

## 🧪 质量保障

| 维度 | 实践 |
|------|------|
| 测试 | pytest + pytest-asyncio，155 用例覆盖全部模块 |
| CI | GitHub Actions：Python 3.11 + 3.12 matrix，ruff lint 门禁，secret scan |
| 安全 | Pre-commit Hook 拦截凭证提交，`.gitignore` 保护 `.env` 和记忆数据 |
| 容错 | LLM 流式中断优雅降级，版权歌曲自动换曲重试（最多 2 次），ChromaDB 降级到 SQLite |
| 代码质量 | ruff All checks passed，零未使用导入，零死代码 |

---

架构决策以 RFC 形式记录在本地 `docs/architecture-reports/` 中（不入库，纯私人草稿）。

---

## 🤝 扩展 & 贡献

Aud.IO 的 Agent 管道采用**插件式架构**，扩展新能力非常自然：

- **接入新 LLM**：OpenAI 兼容协议可直接使用。`llm_client.py` 已内置提供者识别，选择 `LLM_PROVIDER=openai` 即可切换
- **接入新工具**：继承 `BaseTool`，在 `ToolRegistry` 中注册即可被 LLM 自动发现；或通过 MCP 协议接入外部工具服务
- **接入新上下文**：实现 `ContextProvider` 接口，注入到 `ContextAssembler` 的 Provider 列表即可参与 Prompt 组装

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
