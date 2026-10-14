import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": apiProxy },
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    proxy: {
      "/api": apiProxy,
      "/healthz": apiProxy,
    },
  },
});
