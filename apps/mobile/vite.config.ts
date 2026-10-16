import { defineConfig } from "vitest/config";
export default defineConfig({
  server: {
    port: 5178,
    strictPort: true,
    proxy: {
      "/v1": {
        target: process.env.MOBILE_DEV_API ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/uploads": {
        target: process.env.MOBILE_DEV_API ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
