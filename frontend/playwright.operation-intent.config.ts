import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 40000,
  testMatch: [
    "operation-log-intent.spec.ts",
    "operation-logs.spec.ts",
    "meeting-list-sync.spec.ts",
    "department-history.spec.ts",
    "meeting-publication-state.spec.ts",
    "task-list.spec.ts",
    "person-picker-filter.spec.ts",
    "settings-directory.spec.ts",
    "employee-roles.spec.ts",
    "deadline-reminders.spec.ts",
    "dispatch-people-search.spec.ts",
    "dispatch-records-dialog.spec.ts",
  ],
  use: {
    baseURL: "http://127.0.0.1:5204",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5204 --strictPort",
    url: "http://127.0.0.1:5204",
    reuseExistingServer: false,
  },
});
