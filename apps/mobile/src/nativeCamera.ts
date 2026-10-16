import { Capacitor, registerPlugin } from "@capacitor/core";
const camera = registerPlugin<{
  capture(): Promise<{ path: string }>;
  release(options: { path: string }): Promise<void>;
}>("CardCamera");
/** Native guidance is advisory. Server crop proposals remain authoritative. */
export async function guidedCamera(): Promise<Blob | null> {
  if (
    Capacitor.getPlatform() !== "ios" ||
    !Capacitor.isPluginAvailable("CardCamera")
  )
    return null;
  const { path } = await camera.capture();
  try {
    const response = await fetch(Capacitor.convertFileSrc(path));
    if (!response.ok) throw new Error("无法读取拍摄的照片，请重新拍摄");
    return await response.blob();
  } finally {
    await camera.release({ path }).catch(() => {});
  }
}
