import { Capacitor, CapacitorHttp, type HttpOptions } from "@capacitor/core";
function base64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("无法读取照片"));
    reader.readAsDataURL(blob);
  });
}
export async function nativeRequestOptions(
  url: string,
  options: RequestInit,
): Promise<HttpOptions> {
  const headers = Object.fromEntries(new Headers(options.headers).entries());
  let data: unknown = options.body,
    dataType: HttpOptions["dataType"];
  if (options.body instanceof FormData) {
    dataType = "formData";
    headers["content-type"] = "multipart/form-data";
    const values = [];
    for (const [key, value] of options.body.entries())
      values.push(
        typeof value === "string"
          ? { key, value, type: "string" }
          : {
              key,
              value: await base64(value),
              type: "base64File",
              contentType: value.type,
              fileName: value.name,
            },
      );
    data = values;
  }
  return {
    url,
    method: options.method ?? "GET",
    headers,
    data,
    dataType,
    disableRedirects: true,
    connectTimeout: dataType ? 180000 : 30000,
    readTimeout: dataType ? 180000 : 30000,
    responseType: headers.accept === "image/*" ? "arraybuffer" : "json",
  };
}
export const platformFetch: typeof fetch = async (input, options = {}) => {
  if (!Capacitor.isNativePlatform()) return fetch(input, options);
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  const nativeOptions = await nativeRequestOptions(url, options);
  if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const response = await new Promise<
    Awaited<ReturnType<typeof CapacitorHttp.request>>
  >((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    options.signal?.addEventListener("abort", abort, { once: true });
    CapacitorHttp.request(nativeOptions)
      .then(resolve, reject)
      .finally(() => options.signal?.removeEventListener("abort", abort));
  });
  const headers = new Headers(response.headers);
  let body: BodyInit | null = null;
  if (response.status !== 204) {
    if (
      nativeOptions.responseType === "arraybuffer" &&
      !headers.get("content-type")?.includes("json") &&
      response.status < 300
    ) {
      const raw = atob(response.data);
      body = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    } else
      body =
        typeof response.data === "string"
          ? response.data
          : JSON.stringify(response.data);
  }
  return new Response(body, { status: response.status, headers });
};
