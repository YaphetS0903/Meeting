import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import process from "node:process";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5177,
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:8016",
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
