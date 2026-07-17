import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built site works from any path (file://, sub-paths, etc).
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
});
