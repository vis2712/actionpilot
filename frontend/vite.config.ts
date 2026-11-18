import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, /api/* is forwarded to the FastAPI server (same as nginx does in production),
// so the browser only ever talks to one origin and no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://localhost:8001",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
