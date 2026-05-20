# ============================================================
# Aud.IO Backend — FastAPI + ChromaDB + SQLite
#
# 目录结构（容器内）:
#   /app/
#     └── backend/           ← 从构建上下文 ./backend 复制
#           ├── main.py
#           ├── agent/       ← 智能体组件
#           ├── memory/      ← 记忆系统（volume 挂载点）
#           ├── services/    ← 业务编排
#           ├── tools/       ← 工具层
#           └── .env         ← 只读挂载
#
# uvicorn 从 /app 启动 backend.main:app，确保所有 from backend.xxx
# 的绝对导入在容器内正常工作
# ============================================================
FROM python:3.11-slim

# 系统依赖：只装 curl（健康检查用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户
RUN groupadd -r audiosrv && useradd -r -g audiosrv -m -d /app audiosrv

# ---- 依赖安装层 ----
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- 代码层 —— 保持 backend 包名 ----
WORKDIR /app
COPY . /app/backend/

# 数据目录（volume 挂载的目标父目录）
RUN mkdir -p /app/backend/memory && chown -R audiosrv:audiosrv /app

USER audiosrv

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]
