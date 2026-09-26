import { defineConfig } from "@playwright/test";

// Optional: point at a specific browser binary (e.g. a system Chrome) instead of
// the Playwright-bundled Chromium. Left unset the suite uses the bundled browser,
// so a fresh clone does not depend on any machine-specific path.
const executablePath = process.env.PLAYWRIGHT_CHROME_PATH;

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  timeout: 15_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    headless: true,
    launchOptions: executablePath ? { executablePath } : {},
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
