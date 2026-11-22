import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // En dev, l'API du backend est servie à part : on relaie /api vers elle.
    proxy: {
      '/api': 'http://localhost:8000',
      '/mcp': 'http://localhost:8000',
      '/.well-known/oauth-': 'http://localhost:8000',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    // Vitest vide les CSS importés. Exception : l'import en texte (`?raw`),
    // dont styles.test.ts a besoin pour lire les tokens.
    css: { include: /\.css\?raw$/ },
  },
})
