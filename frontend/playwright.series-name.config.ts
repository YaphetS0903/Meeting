import { defineConfig } from "@playwright/test";
import base from "./playwright.dispatch-people.config";

export default defineConfig({
  ...base,
  testMatch: ["meeting-series-name.spec.ts"],
});
