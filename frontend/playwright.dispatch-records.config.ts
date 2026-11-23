import { defineConfig } from "@playwright/test";
import base from "./playwright.dispatch-people.config";

export default defineConfig({
  ...base,
  testMatch: [
    "dispatch-records-dialog.spec.ts",
    "dispatch-people-search.spec.ts",
  ],
});
