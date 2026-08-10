import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * Le serveur de développement relaie `/api` et `/ws` vers le backend.
 *
 * Ce n'est pas qu'un confort : sans relais, le frontend (port 5173) et l'API
 * (port 8000) seraient deux origines distinctes, et le cookie de session
 * exigerait `SameSite=None; Secure` — donc HTTPS en développement. Le relais
 * fait de tout cela une seule origine, et le mode développement se comporte
 * exactement comme la production, où le frontend est servi par le même hôte.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
