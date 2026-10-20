const HELPER_URL = "http://127.0.0.1:8766";
const CLIENT_HEADERS = { "X-Scanner-Client": "cardsp-web-v1" };

export function usesLocalScannerHelper(): boolean {
  return typeof window !== "undefined" && !["localhost", "127.0.0.1", "", "[::1]"].includes(window.location.hostname);
}

async function helperRequest(path: string, init: RequestInit, timeout: number, signal?: AbortSignal): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${HELPER_URL}${path}`, {
      ...init, credentials: "omit", redirect: "error",
      headers: { ...CLIENT_HEADERS, ...init.headers },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
    });
  } catch {
    throw new Error("无法连接本机扫描助手。请确认助手已运行，并允许浏览器访问本机网络，然后重新连接。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `本机扫描失败 (${response.status})`);
  }
  return response;
}

export async function getLocalScannerDevice(signal?: AbortSignal): Promise<{ name: string; serial: string; token: string }> {
  const response = await helperRequest("/v1/device", {}, 30_000, signal);
  const device = await response.json();
  if (typeof device.token !== "string" || !device.token) throw new Error("扫描助手版本不兼容，请更新本机助手。");
  return device;
}

export type LocalScannerImage = { file: File; sourceResolution: Record<string, unknown> };

export async function scanLocalScanner(dpi: 300 | 600, preview: boolean, token: string, signal?: AbortSignal): Promise<LocalScannerImage> {
  const response = await helperRequest("/v1/scan", {
    method: "POST", headers: { "Content-Type": "application/json", "X-Scanner-Token": token },
    body: JSON.stringify({ dpi, preview }),
  }, preview ? 105_000 : 255_000, signal);
  if (!response.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("扫描助手未返回有效 PNG 图片。");
  let sourceResolution: Record<string, unknown> = {};
  const rawMetadata = response.headers.get("X-Scan-Resolution");
  if (rawMetadata && rawMetadata.length <= 4096) {
    try {
      const value = JSON.parse(rawMetadata);
      if (value && typeof value === "object" && !Array.isArray(value)) sourceResolution = value;
    } catch { /* Unknown resolution remains an explicit cloud fallback. */ }
  }
  const blob = await response.blob();
  return { file: new File([blob], "canon-scan.png", { type: "image/png" }), sourceResolution };
}
