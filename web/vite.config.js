import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：/api 代理到本地后端 8100；生产：dist 由后端挂载，axios 同源即可
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8100',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 2500,
  },
})
