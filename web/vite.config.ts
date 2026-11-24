import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时前端跑 5173，接口代理到 Python 服务 8765；
// 构建产物在 web/dist，由 server.py 直接托管（含 SPA 回退）。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
})