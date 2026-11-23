import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: ["meeting-editor-actions.spec.ts", "mail-cc-task-order.spec.ts"],
  workers: 1,
  timeout: 40000,
  use: {
    baseURL: "http://127.0.0.1:5196",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5196 --strictPort",
    url: "http://127.0.0.1:5196",
    reuseExistingServer: false,
  },
});
