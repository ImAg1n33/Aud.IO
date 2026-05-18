import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],

  // 开发服务器代理 —— 将 /api 前缀的请求转发到后端，消除前端硬编码地址
  // 生产环境建议用 nginx/Caddy 做反向代理，或设置 VITE_API_BASE_URL 环境变量
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        // /api/v1/agent/respond/stream → /v1/agent/respond/stream
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
