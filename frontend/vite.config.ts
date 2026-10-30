import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API runs on http://localhost:8000 (uvicorn); Vite forwards these paths to it,
// so the app and the API share one origin, exactly like in production.
const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": api, "/auth": api, "/healthz": api },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
