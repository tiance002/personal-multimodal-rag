import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/weknora-port",
  workers: 1,
  timeout: 15_000,
  use: {
    baseURL: "http://127.0.0.1:5189",
    headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROME_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROME_PATH } : {},
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5189 --strictPort",
    env: {
      VITE_WEKNORA_API_BASE: "/api/v1",
      VITE_API_PROXY_TARGET: "http://127.0.0.1:56425",
    },
    url: "http://127.0.0.1:5189",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
