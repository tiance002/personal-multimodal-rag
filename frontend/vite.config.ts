import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = process.env.VITE_API_PROXY_TARGET?.trim();
const proxy = apiProxy ? {
  "/api": { target: apiProxy, changeOrigin: true },
  "/healthz": { target: apiProxy, changeOrigin: true },
  "/readyz": { target: apiProxy, changeOrigin: true },
} : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    ...(proxy ? { proxy } : {}),
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    ...(proxy ? { proxy } : {}),
  },
});
