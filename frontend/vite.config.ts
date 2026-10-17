import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const target = process.env.VITE_API_TARGET ?? 'http://backend:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': { target, changeOrigin: true },
      '/healthz': { target, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // Routes are lazy; vendor code is split so no single chunk passes the 500 kB warning.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('@mantine')) return 'mantine';
          if (/node_modules\/(react|react-dom|react-router|scheduler)\//.test(id)) return 'react';
          if (/node_modules\/(i18next|react-i18next|@tanstack|zustand)\//.test(id)) return 'data';
          return undefined;
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
