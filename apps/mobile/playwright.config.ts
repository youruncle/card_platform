import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "tests/e2e",
  workers: 1,
  use: {
    channel: "chrome",
    baseURL: "http://127.0.0.1:5178",
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
  },
  reporter: "list",
  outputDir: "../../work/mobile/playwright",
});
