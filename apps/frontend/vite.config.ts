import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_URL points the SPA at the FastAPI backend; the default matches the
// backend's local dev port (ADR 0001: backend 8002, frontend 3002).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3002,
    strictPort: true,
  },
  define: {
    "import.meta.env.VITE_API_URL": JSON.stringify(
      process.env.VITE_API_URL ?? "http://localhost:8002",
    ),
  },
});
