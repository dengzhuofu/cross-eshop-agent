import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 本地开发时把 /api 与 /healthz 代理到 FastAPI 后端(固定 127.0.0.1:8000),
// 因此前端代码里只写相对路径,天然规避跨域问题。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
