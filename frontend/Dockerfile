# ============================================================
# Aud.IO 前端 — 多阶段构建
# Stage 1: Node.js 构建 Vue/Vite 产物
# Stage 2: Nginx 托管静态文件 + 反向代理
# ============================================================

# ---- Stage 1: Build ----
FROM node:22-alpine AS builder

WORKDIR /build

# 依赖层缓存
COPY package.json package-lock.json* ./
RUN npm ci --silent

# 源码 + 构建
COPY . .
RUN npm run build

# ---- Stage 2: Serve ----
FROM nginx:stable-alpine

# 删除 Nginx 默认页面
RUN rm -rf /usr/share/nginx/html/*

# 复制 Vite 构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# Nginx 自带 healthcheck — alpine 版本用 wget 更轻
# 注意: 必须用 127.0.0.1 而非 localhost —— alpine 容器内 localhost 解析为
# IPv6 ::1，而 nginx 只监听 IPv4 (0.0.0.0)，会导致健康检查误报 unhealthy
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
