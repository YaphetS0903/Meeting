import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testIgnore: [
    "oauth-return.spec.ts",
    "theme.spec.ts",
    "workflow.spec.ts",
    "zz-password.spec.ts",
    "load-failure.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  timeout: 40000,
  outputDir: "./test-results/mock",
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5202",
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5202 --strictPort",
    url: "http://127.0.0.1:5202",
    reuseExistingServer: false,
    env: { BACKEND_URL: "http://127.0.0.1:1" },
  },
});
