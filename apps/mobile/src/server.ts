import { Capacitor } from "@capacitor/core";
import { validateOrigin } from "./api";
export const CLOUD_ORIGIN = "https://cardsp.cn";

// Production and native apps always use the cloud. Browser development can
// explicitly target a loopback service; the old VITE_API_ORIGIN is ignored.
export function serverOrigin(): string {
  if (import.meta.env.DEV && !import.meta.env.PROD && !Capacitor.isNativePlatform()) {
    const override = import.meta.env.VITE_MOBILE_DEV_ORIGIN;
    if (override) {
      try {
        const origin = validateOrigin(override, true);
        if (["localhost", "127.0.0.1", "[::1]"].includes(new URL(origin).hostname))
          return origin;
      } catch { /* An invalid development override cannot change the destination. */ }
    }
  }
  return CLOUD_ORIGIN;
}
