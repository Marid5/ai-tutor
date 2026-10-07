import { defineConfig, devices } from '@playwright/test';

/**
 * Browser tests run against the real backend and the built client on a
 * throwaway database (scripts/e2e-server.sh): the server grades every closed
 * step and decides what comes next, so a mock would test nothing that matters.
 *
 * PW_CHROMIUM_ARGS (optional, space-separated) passes extra Chromium flags,
 * for sandboxes where the default process model cannot start. With
 * --single-process this is a degraded fallback for restricted sandboxes
 * (traces and video are unavailable); CI runs without it.
 */
const chromiumArgs = (process.env.PW_CHROMIUM_ARGS ?? '').split(/\s+/).filter(Boolean);
// E2E_PORT moves the test server off 8765 when that port is taken.
const PORT = Number(process.env.E2E_PORT || 8765);

export default defineConfig({
  testDir: './e2e',
  // One server, one database: tests are independent (a fresh account each),
  // but running them one at a time keeps a failure easy to read.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  forbidOnly: Boolean(process.env.CI),
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    launchOptions: { args: chromiumArgs },
  },
  // Chromium at both widths: what differs is the layout (bottom tab bar on
  // phones, header tabs on wide screens), not the engine.
  projects: [
    {
      name: 'mobile',
      // A touch phone: coarse pointer, so the key badges are hidden as on a real device.
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: {
    // bash, not sh: the script relies on bash features (pipefail, traps).
    command: 'bash ../scripts/e2e-server.sh',
    env: { E2E_PORT: String(PORT) },
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    // SIGTERM lets the script stop the server and delete its database.
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
