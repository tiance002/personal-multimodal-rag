import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/api-configuration",
  workers: 1,
  timeout: 15_000,
  use: {
    baseURL: "http://127.0.0.1:5190",
    headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROME_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROME_PATH } : {},
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5190 --strictPort",
    env: {
      ...process.env,
      VITE_WEKNORA_API_BASE: undefined,
      VITE_API_PROXY_TARGET: undefined,
    },
    url: "http://127.0.0.1:5190",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
