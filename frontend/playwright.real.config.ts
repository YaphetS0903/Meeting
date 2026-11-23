import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
const backend = fileURLToPath(new URL("../backend", import.meta.url));
const python = path.join(
  backend,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
export default defineConfig({
  testDir: "./tests",
  testMatch: [
    "oauth-return.spec.ts",
    "theme.spec.ts",
    "workflow.spec.ts",
    "zz-password.spec.ts",
    "load-failure.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  outputDir: "./test-results/real",
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile: "../../.validation/system-fixes-20261105/real-results.json",
      },
    ],
  ],
  use: {
    baseURL: "http://127.0.0.1:5177",
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `"${python}" -m tests.browser_server`,
      cwd: backend,
      url: "http://127.0.0.1:8017/api/health",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --port 5177 --strictPort",
      url: "http://127.0.0.1:5177",
      env: { BACKEND_URL: "http://127.0.0.1:8017" },
      reuseExistingServer: false,
    },
  ],
});
