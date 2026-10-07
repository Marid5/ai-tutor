import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Unit tests live next to the code; browser tests (e2e/) belong to Playwright.
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
