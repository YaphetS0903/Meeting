import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import process from "node:process";
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5176,
    proxy: { "/api": process.env.BACKEND_URL || "http://127.0.0.1:8016" },
  },
  build: { chunkSizeWarningLimit: 1200 },
});
