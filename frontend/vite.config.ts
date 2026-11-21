import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The dev server proxies API + WebSocket traffic to the FastAPI backend,
// so the app is same-origin in development and no CORS/WS URL juggling.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8001', ws: true },
    },
  },
})
