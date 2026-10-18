import react from '@vitejs/plugin-react';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

const target = process.env.VITE_API_TARGET ?? 'http://backend:8000';
/** Public base path: `/` for Docker, `/a0-to-domestic/` for GitHub Pages. */
const base = process.env.VITE_BASE || '/';
/** `local` builds the web version (Pyodide in a worker, IndexedDB; SPEC 17bis). */
const isLocal = process.env.VITE_MODE === 'local';

/** Emits `sw.js` with the app shell of this build precached (local builds only). */
function serviceWorker(): Plugin {
  return {
    name: 'a0td-service-worker',
    apply: 'build',
    generateBundle(_, bundle) {
      if (!isLocal) return;
      const files = Object.keys(bundle).filter((f) => !f.endsWith('.map'));
      const precache = ['./', ...files];
      const buildId = createHash('sha1').update(files.join('|')).digest('hex').slice(0, 10);
      const template = readFileSync(
        fileURLToPath(new URL('./sw/sw.template.js', import.meta.url)),
        'utf8',
      );
      this.emitFile({
        type: 'asset',
        fileName: 'sw.js',
        source: template
          .replace('__BUILD_ID__', buildId)
          .replace('__PRECACHE__', JSON.stringify(precache)),
      });
    },
  };
}

export default defineConfig({
  base,
  // public/ only holds py/ (Pyodide, wheels, a0td.zip): the Docker build must not ship it.
  publicDir: isLocal ? 'public' : false,
  plugins: [react(), serviceWorker()],
  worker: { format: 'es' },
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
