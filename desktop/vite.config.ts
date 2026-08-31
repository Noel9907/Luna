import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron/simple'
import path from 'node:path'

export default defineConfig({
  resolve: {
    alias: {
      '@shared': path.resolve(__dirname, '../shared'),
      '@': path.resolve(__dirname, 'src'),
    },
  },
  plugins: [
    react(),
    electron({
      main: { entry: 'electron/main.ts' },
      preload: { input: path.join(__dirname, 'electron/preload.ts') },
    }),
  ],
  server: { port: 5174 },
})
