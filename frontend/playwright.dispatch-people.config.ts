import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: ["dispatch-people-search.spec.ts", "settings-directory.spec.ts"],
  workers: 1,
  timeout: 40000,
  use: {
    baseURL: "http://127.0.0.1:5197",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5197 --strictPort",
    url: "http://127.0.0.1:5197",
    reuseExistingServer: false,
  },
});
