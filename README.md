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
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/ChromaDB-Vector_Memory-FF6F00?style=flat-square&logo=database&logoColor=white" alt="ChromaDB">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square" alt="PRs Welcome">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License">
</p>

---

## ✨ 核心特性

<table>
  <tr>
    <td width="50%">
      <h3>🎧 AI DJ 对话</h3>
      <p>不是冷冰冰的播放器指令，而是像电台 DJ 一样的自然对话。说一句"来点轻松的爵士"，Aud.IO 理解你的语境，用打字机效果逐字说出推荐理由，然后自动搜索并播放。</p>
    </td>
    <td width="50%">
      <h3>🧠 语义记忆</h3>
      <p>记住你喜欢过什么、什么心情下听过什么、常在什么时候打开它。即使你说"来点上次那种感觉的"，它也能从向量记忆库中找回最相关的那次交互。</p>
    </td>
  </tr>
  <tr>
    <td>
      <h3>🎨 Nothing Design</h3>
      <p>受 Nothing 品牌美学启发的双模式界面 —— 暗色模式的深邃哑光黑、亮色模式的通透冷白，三层视觉层级、无衬线字体、低饱和度色彩，让音乐成为唯一的焦点。</p>
    </td>
    <td>
      <h3>⚡ 流式响应</h3>
      <p>基于 SSE (Server-Sent Events) 的逐字流式输出，配合 Web Audio API 的淡入淡出过渡。连接中断自动重试，推流中途断开则优雅保留已输出文本。</p>
    </td>
  </tr>
</table>

---

## 🏗️ 全栈架构

```
用户输入 → 意图分类 → 上下文组装 (5 个 Provider 插件) → LLM 流式推理
                                                              ↓
    ← SSE 打字机流 + 音乐播放 ← 工具执行 (网易云搜索 + 版权重试) ←
                                                              ↓
                      情节记忆双写 (ChromaDB 向量 + SQLite 兼容)
```

| 层 | 技术选型 | 为什么 |
|----|----------|--------|
| 前端 | Vue 3 + Vite 8 | 轻量 SPA，无组件库依赖，纯手写 CSS |
| 后端 | FastAPI + Uvicorn | 异步原生支持，SSE 流式零额外开销 |
| LLM | DeepSeek（OpenAI 兼容协议） | 低成本、流式输出、中文理解出色 |
| 向量记忆 | ChromaDB + ONNX all-MiniLM-L6-v2 | 完全本地离线，零网络依赖，首次自动下载 ~80MB 模型 |
| 情节记忆 | SQLite（双写兼容） | 嵌入式零配置，SQL 聚合统计补充向量检索 |
| 音乐服务 | NetEase Cloud Music API | 曲库覆盖广，支持扫码登录 |

---

## 🚀 5 分钟跑起来

### 前置条件

- Python 3.11+（推荐 Conda 环境）
- Node.js 18+
- [可选] 本地运行的 [NeteaseCloudMusicApi](https://github.com/Binaryify/NeteaseCloudMusicApi) 实例（音乐播放需要）

### 安装 & 启动

```bash
# 1. 克隆仓库
git clone https://github.com/ImAg1n33/Aud.IO.git
cd Aud.IO

# 2. 后端
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env                  # 编辑填入 LLM_API_KEY

uvicorn backend.main:app --reload --port 8001

# 3. 前端（新终端）
cd frontend && npm install && npm run dev

# 4. 打开 http://localhost:5173
```

---

## 📂 项目导览

```
Aud.IO/
├── backend/
│   ├── main.py                          # 应用入口
│   ├── api/routes_agent.py              # API 路由层
│   ├── services/assistant_service.py    # ★ 核心编排管道
│   ├── agent/                           # 智能体组件
│   │   ├── llm_client.py               #   LLM 调用 + 流式重试
│   │   ├── context_assembler.py        #   ★ 插件式上下文组装
│   │   ├── intent_classifier.py        #   意图分类（规则引擎）
│   │   ├── tool_executor.py            #   工具调度引擎
│   │   └── memory_manager.py           #   用户画像异步更新
│   ├── memory/                          # 记忆系统
│   │   ├── episodic_memory.py          #   ★ ChromaDB 向量记忆 + MoodDetector
│   │   ├── embedding.py                #   向量嵌入抽象层
│   │   ├── conversation_memory.py      #   短期对话记忆
│   │   └── profile_schema.py           #   用户画像模型
│   └── tools/                           # 工具层
│       ├── music_tool.py               #   网易云音乐搜索 & 播放
│       ├── netease_api.py              #   网易云 API 封装
│       └── login_netease.py            #   扫码登录
├── frontend/
│   ├── vite.config.js                   # Vite 配置 + API 代理
│   └── src/
│       ├── App.vue                      # ★ 完整 UI：打字机 + 音频播放器
│       └── style.css                    # Nothing Design 暗/亮双模式
├── tests/                               # pytest 测试套件
├── docs/                                # 架构文档 & API 文档
└── .github/workflows/ci.yml             # CI 自动测试 + 安全扫描
```

---

## 🧪 质量保障

| 维度 | 实践 |
|------|------|
| 测试 | pytest + pytest-asyncio，覆盖核心编排管道、记忆系统、工具调度、意图分类 |
| CI | GitHub Actions 自动运行测试 + API Key 泄露扫描 |
| 安全 | Pre-commit Hook 拦截敏感文件提交，`.gitignore` 保护个人数据 |
| 容错 | LLM 流式中断优雅降级，版权歌曲自动换曲重试，ChromaDB → SQLite 查询自动 fallback |

---

## 🚀 路线图

- [x] LLM 流式对话 + SSE 打字机效果
- [x] 网易云音乐搜索 & 播放
- [x] Nothing Design 暗/亮双模式 UI
- [x] ChromaDB 向量语义记忆 + 自动心情检测
- [ ] 天气感知推荐（代码已预留接口）
- [ ] TTS 语音合成播报（代码已预留接口）
- [ ] Docker 一键部署
- [ ] 多用户会话隔离
- [ ] 网页端管理后台

---

## 🤝 扩展 & 贡献

Aud.IO 的 Agent 管道采用 **插件式架构**，扩展新能力非常自然：

- **接入新 LLM**：实现 OpenAI 兼容协议的任意模型均可直接使用（当前跑通 DeepSeek）；其他协议需在 `llm_client.py` 中增加适配层
- **接入新工具**：继承 `BaseTool`，在 `ToolRegistry` 中注册即可被 LLM 自动发现和调用
- **接入新上下文**：实现 `ContextProvider` 接口，注入到 `ContextAssembler` 的 Provider 列表即可参与 Prompt 组装

```python
# 添加一个新的上下文来源 —— 就这么简单
class WeatherProvider(ContextProvider):
    name = "weather"
    async def get_context(self, intent, user_input, metadata):
        weather = await fetch_weather()
        return f"[当前天气]\n{weather}  # LLM 会自动理解并利用这段信息"

assembler.providers.append(WeatherProvider())
```

欢迎提 Issue 和 PR。

---

## 📄 License

MIT
