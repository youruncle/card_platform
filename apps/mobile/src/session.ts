import { Capacitor, registerPlugin } from "@capacitor/core";
import type { Session } from "./types";
const vault = registerPlugin<{
  read(): Promise<{ value?: string }>;
  write(options: { value: string }): Promise<void>;
  clear(): Promise<void>;
}>("SessionVault");
// Browser previews deliberately keep credentials in memory only.
let memory: Session | null = null;
export const sessionVault = {
  async load(): Promise<Session | null> {
    if (!Capacitor.isNativePlatform()) return memory;
    const { value } = await vault.read();
    if (!value) return null;
    try {
      return JSON.parse(value) as Session;
    } catch {
      await vault.clear();
      return null;
    }
  },
  async save(session: Session) {
    if (Capacitor.isNativePlatform())
      await vault.write({ value: JSON.stringify(session) });
    else memory = session;
  },
  async clear() {
    memory = null;
    if (Capacitor.isNativePlatform()) await vault.clear();
  },
};
