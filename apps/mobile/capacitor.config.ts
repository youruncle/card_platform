import type { CapacitorConfig } from "@capacitor/cli";
const config: CapacitorConfig = {
  appId: "cn.cardsp.alpha.local",
  appName: "我的卡库",
  webDir: "dist",
  plugins: { CapacitorHttp: { enabled: false } },
  ios: { contentInset: "automatic" },
};
export default config;
