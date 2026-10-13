import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/weknora-port",
  workers: 1,
  fullyParallel: false,
  timeout: 20_000,
  use: { baseURL: "http://127.0.0.1:5184", headless: true },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5184 --strictPort",
    url: "http://127.0.0.1:5184",
    reuseExistingServer: false,
    timeout: 30_000,
    env: { ...process.env, VITE_RAG_IMPLEMENTATION: "weknora_python" },
  },
});
