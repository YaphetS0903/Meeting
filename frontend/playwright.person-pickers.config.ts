import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 60000,
  testMatch: [
    "person-picker-filter.spec.ts",
    "meeting-host-selection.spec.ts",
    "meeting-owner-selection.spec.ts",
    "dispatch-people-search.spec.ts",
    "mail-cc-task-order.spec.ts",
    "dispatch-records-dialog.spec.ts",
    "task-overview-order.spec.ts",
    "settings-directory.spec.ts",
  ],
  use: {
    baseURL: "http://127.0.0.1:5198",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5198 --strictPort",
    url: "http://127.0.0.1:5198",
    reuseExistingServer: false,
  },
});
