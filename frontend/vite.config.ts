import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// In development Vite serves the client and forwards API calls to the
// FastAPI backend, so the browser sees one origin and the session cookie works.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    outDir: 'dist',
    // Never inline assets as data: URIs into scripts or styles; the CSP serves
    // everything from the app's own origin.
    assetsInlineLimit: 0,
  },
});
