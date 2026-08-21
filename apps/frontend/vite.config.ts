import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/** VITE_API_URL points the SPA at the backend; default matches ADR 0001 ports (8002/3002). */
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
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
