import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend runs on port 8000 (`uv run uvicorn gar_backend.main:app --reload`).
// Proxying the backend's HTTP surface from the Vite dev server avoids CORS
// configuration in the backend and keeps the frontend's fetch / EventSource
// calls relative-URL clean.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/runs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/healthz': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
