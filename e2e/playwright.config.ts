import { defineConfig, devices } from '@playwright/test';

// The tests run inside the compose network against the `full` image (SPEC 15.5).
export default defineConfig({
  testDir: './tests',
  outputDir: process.env.E2E_OUTPUT_DIR ?? '/tmp/e2e-results',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://app-full:8080',
    locale: 'en-GB', // region GB -> A4 + mm defaults (en-US would pick LETTER, WP-13),
    acceptDownloads: true,
    trace: process.env.CI ? 'on-first-retry' : 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
