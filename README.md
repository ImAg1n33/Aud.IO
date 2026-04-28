# Aud.IO

Aud.IO 是一个具备 FastAPI 后端与前端界面的 AI 语音助手项目。

## 项目结构

- backend: FastAPI 接口、智能体编排、记忆上下文与工具封装
   - backend/api: 路由层
   - backend/services: 业务编排层
- frontend: 前端 UI（建议 Vue 3 或 React，Nothing Design 风格）
- docs: 架构说明与 API 文档

## 快速开始

1. 创建并激活 Python 虚拟环境。
2. 安装后端依赖：
   - pip install -r backend/requirements.txt
3. 复制环境变量模板：
   - copy backend/.env.example backend/.env
4. （可选）复制本地记忆模板：
   - copy backend/memory/taste.example.md backend/memory/taste.md
   - copy backend/memory/routines.example.md backend/memory/routines.md
5. 启动 API 服务：
   - uvicorn backend.main:app --reload --port 8001

## 测试

1. 安装开发依赖：
   - pip install -r requirements-dev.txt
2. 运行测试：
   - pytest

当前测试覆盖：

- CORS 解析逻辑
- Agent 路由响应协议
- Assistant 服务编排
- MemoryManager 异步 JSON Patch 更新流程

## CI

GitHub Actions 配置位于：

- .github/workflows/ci.yml

触发条件：push 到 main 或 PR。执行内容：

1. pytest
2. scripts/security_scan.py

## LLM 环境策略（开源友好）

在 backend/.env.example 中使用通用的环境变量格式：

- LLM_PROVIDER
- LLM_BASE_URL
- LLM_MODEL
- LLM_API_KEY

推荐理由：

- 统一适配 DeepSeek、OpenAI、Anthropic 以及后续模型
- 协作者更容易上手
- 只提交 backend/.env.example，backend/.env 保持本地

### DeepSeek 本地示例

在 backend/.env 中配置：

- LLM_PROVIDER=deepseek
- LLM_BASE_URL=https://api.deepseek.com
- LLM_MODEL=deepseek-chat
- LLM_API_KEY=your_real_deepseek_key

当 LLM_API_KEY 为空时，也可以使用 DEEPSEEK_API_KEY 作为兜底。

### CORS 白名单

- 在 backend/.env 中配置 `CORS_ALLOW_ORIGINS`，用逗号分隔。
- 默认包含常见本地开发端口与 `null`（用于 file:// 本地打开页面的场景）。

## 开源安全清单

- 不要提交任何 API Key（Claude/Fish Audio/OpenAI 等）。
- 真实凭据仅保留在本地 .env。
- 只提交 backend/.env.example 作为模板。
- 个人记忆文件保持本地，例如 backend/memory/taste.md。
- 推送前运行 git status，确认没有敏感文件被暂存。

详细运行手册：

- docs/security-playbook.md

提交前扫描：

- VS Code 任务：Scan Secrets (Tracked Files)

Pre-commit 保护：

- VS Code 任务：Install Git Hooks（每个仓库只需执行一次）
- Hook 文件：.githooks/pre-commit

## 初始 API 路由

- GET /health
- GET /ready
- POST /v1/agent/respond
   - reply 为严格 JSON 对象，包含字段：
      - analysis: string
      - answer: string
      - actions: string[]
      - provider: string
      - model: string

## 后续迭代计划

- 在 backend/agent/llm_client.py 中接入真实模型调用
- 在 backend/tools 中实现 NetEase、天气与 TTS 工具
- 构建前端界面并接入 API
- 扩展架构文档与时序图
