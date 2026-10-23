import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:5177",
    browserName: "chromium",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      env: {
        MONITOR_STATE_DIR: "/tmp/meeting-actions-e2e-monitor",
        ALERT_WEBHOOK_URL: "",
        ALERT_WEBHOOK_TOKEN: "",
        WECOM_CORP_ID: "",
        WECOM_AGENT_ID: "",
        WECOM_SECRET: "",
        SMTP_HOST: "",
        SMTP_FROM: "",
        SMTP_USERNAME: "",
        SMTP_PASSWORD: "",
      },
      command:
        "cd ../backend && DATABASE_URL=sqlite:////tmp/meeting-actions-e2e.db STORAGE_DIR=/tmp/meeting-actions-e2e-storage uv run python -m tests.seed_e2e && DATABASE_URL=sqlite:////tmp/meeting-actions-e2e.db STORAGE_DIR=/tmp/meeting-actions-e2e-storage CORS_ORIGINS='[\"http://127.0.0.1:5177\"]' uv run uvicorn app.main:app --host 127.0.0.1 --port 8017",
      url: "http://127.0.0.1:8017/api/health",
      reuseExistingServer: false,
    },
    {
      command: "BACKEND_URL=http://127.0.0.1:8017 npm run dev -- --port 5177",
      url: "http://127.0.0.1:5177",
      reuseExistingServer: false,
    },
  ],
});
