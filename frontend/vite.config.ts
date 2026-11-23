import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";

// En local: host "localhost" y API en http://localhost:8000.
// En docker-compose: DEV_HOST=0.0.0.0 (para publicar el puerto) y API_PROXY_TARGET=http://api:8000.
const host = process.env.DEV_HOST ?? "localhost";
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  server: {
    host,
    port: 5173,
    // El navegador llama a /api/... y Vite lo reenvía a la API (sin problemas de CORS).
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  // Dos páginas: el mapa con el globo (index.html → src/pages/globe.ts) y la pestaña de incendios
  // (fires.html → src/pages/fires.ts).
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        fires: fileURLToPath(new URL("./fires.html", import.meta.url)),
      },
    },
  },
});
