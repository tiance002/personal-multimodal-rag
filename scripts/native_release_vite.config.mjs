// Preview the existing bundle only; never load frontend .env or widen the host.
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const apiPort = process.env.RAG_NATIVE_API_PORT;
const uiPort = process.env.RAG_NATIVE_UI_PORT;
if (!/^\d{1,5}$/.test(apiPort ?? '') || !/^\d{1,5}$/.test(uiPort ?? '') ||
    +apiPort < 1 || +apiPort > 65535 || +uiPort < 1 || +uiPort > 65535) {
  throw new Error('EXPLICIT_NATIVE_PORTS_REQUIRED');
}
export default {
  root,
  envDir: false,
  preview: {
    host: '127.0.0.1', port: +uiPort, strictPort: true,
    proxy: { '/api': `http://127.0.0.1:${apiPort}`, '/healthz': `http://127.0.0.1:${apiPort}` },
  },
};
