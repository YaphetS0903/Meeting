import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "task-sheet-import.spec.ts",
  workers: 1,
  timeout: 40000,
  use: {
    baseURL: "http://127.0.0.1:5194",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5194 --strictPort",
    url: "http://127.0.0.1:5194",
    reuseExistingServer: false,
  },
});
