import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const proxyTarget = process.env.VITE_DEV_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/docs": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/redoc": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/openapi.json": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
