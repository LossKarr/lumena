import { defineConfig } from "vite";

// Vite dev server proxies /api/* to the FastAPI backend.
// In production, FastAPI directly serves /static/* — no Vite needed.
const BACKEND_PORT = process.env.LUMENA_PORT || "8080";
const VITE_PORT = parseInt(process.env.LUMENA_VITE_PORT || "3000", 10);

export default defineConfig({
  root: ".",
  publicDir: false,
  server: {
    port: VITE_PORT,
    open: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
      "/ws": {
        target: `ws://127.0.0.1:${BACKEND_PORT}`,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: "index.html",
    },
  },
});
