import { defineConfig, devices } from "@playwright/test"

/**
 * End-to-end tests against a running stack (API + worker + scheduler + web).
 *   FLOWFORGE_WEB_URL  dashboard URL (default http://localhost:3000)
 *   FLOWFORGE_API_URL  API URL used for test setup (default http://127.0.0.1:8000)
 *   PW_CHANNEL         browser channel; defaults to the installed, signed Microsoft Edge on Windows (unsigned
 *                      downloaded browsers can be blocked by Windows Smart App Control) and bundled Chromium elsewhere.
 */
const channel = process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined)

export default defineConfig({
  testDir: "./specs",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.FLOWFORGE_WEB_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      testIgnore: /(responsive|screenshots)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      testMatch: /responsive\.spec\.ts/,
      use: { ...devices["Pixel 7"], channel },
    },
    {
      name: "screenshots",
      testMatch: /screenshots\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
    },
  ],
})
