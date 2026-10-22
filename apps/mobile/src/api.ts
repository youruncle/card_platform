import {
  captureForm,
  type CaptureDraft,
} from "../../../packages/card-core/src/capture";
import { platformFetch, type TimedRequestInit } from "./transport";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public retryAfter?: number,
  ) {
    super(message);
  }
}
export function validateOrigin(value: string, dev = false): string {
  const url = new URL(value.trim());
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    (url.protocol !== "https:" &&
      !(dev && local && url.protocol === "http:")) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("请填写 HTTPS 服务地址（不包含路径、账号或密码）");
  }
  return url.origin;
}
export function protectedUrl(origin: string, path: string): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
    throw new Error("无效的接口地址");
  const url = new URL(path, origin);
  if (
    url.origin !== origin ||
    !(url.pathname.startsWith("/v1/") || url.pathname.startsWith("/uploads/"))
  )
    throw new Error("拒绝访问其他服务");
  return url.href;
}
export class MobileApi {
  private closed = false;
  private libraryListeners = new Set<() => void>();
  subscribeLibrary(listener: () => void) {
    this.libraryListeners.add(listener);
    return () => { this.libraryListeners.delete(listener); };
  }
  private invalidateLibrary() {
    for (const listener of this.libraryListeners) listener();
  }
  private controllers = new Set<AbortController>();
  constructor(
    public readonly origin: string,
    private token: string,
    private onExpired: () => void,
    private transport: typeof fetch = platformFetch,
  ) {}
  async request<T>(
    path: string,
    options: RequestInit = {},
    image = false,
    timeoutMs?: number,
  ): Promise<T> {
    if (this.closed) throw new Error("会话已结束");
    if (options.signal?.aborted) throw new Error("请求已取消");
    const url = protectedUrl(this.origin, path);
    const controller = new AbortController();
    this.controllers.add(controller);
    const cancel = () => controller.abort();
    options.signal?.addEventListener("abort", cancel, { once: true });
    const timeout = setTimeout(
      () => controller.abort(),
      timeoutMs ?? (options.body instanceof FormData ? 180000 : 30000),
    );
    const headers = new Headers(options.headers);
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    try {
      const requestOptions: TimedRequestInit = {
        ...options,
        headers,
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        requestTimeoutMs: timeoutMs,
      };
      const response = await this.transport(url, requestOptions);
      if (this.closed) throw new Error("会话已结束");
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        if (response.status === 401 && this.token) {
          this.dispose();
          this.onExpired();
        }
        const detail = body?.detail;
        const message =
          typeof detail === "string"
            ? detail
            : typeof detail?.message === "string"
              ? detail.message
              : response.status === 422
                ? "请检查必填字段和数值范围"
                : `请求失败（${response.status}）`;
        const seconds = Number(
          detail?.retry_after_seconds ?? response.headers.get("Retry-After"),
        );
        throw new ApiError(
          response.status,
          message,
          typeof detail?.code === "string" ? detail.code : undefined,
          Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : undefined,
        );
      }
      const value =
        response.status === 204
          ? undefined
          : image
            ? await response.blob()
            : await response.json();
      if (this.closed) throw new Error("会话已结束");
      if (!["GET", "HEAD"].includes((options.method ?? "GET").toUpperCase())) this.invalidateLibrary();
      return value as T;
    } catch (error) {
      if (path === "/v1/mobile/auth/login" && !(error instanceof ApiError)) {
        const failure = error as {
          name?: string;
          message?: string;
          code?: string;
        } | null;
        const description = `${failure?.message ?? ""} ${failure?.code ?? ""}`;
        if (
          failure?.name === "AbortError" ||
          /timed?\s*out|timeout|-1001/i.test(description)
        )
          throw new Error("登录连接超时，请检查网络后重试。");
        if (/ssl|tls|certificate|secure connection|证书|-120[0-6]/i.test(description))
          throw new Error("无法建立安全连接，请检查设备日期和网络后重试。");
        throw new Error("无法连接云端，请检查网络后重试。");
      }
      if (error && typeof error === "object" && "name" in error && error.name === "AbortError")
        throw new Error("请求已取消或超时，草稿会保留，可重新尝试");
      throw error;
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", cancel);
      this.controllers.delete(controller);
    }
  }
  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }
  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }
  upload<T>(path: string, front: Blob, back: Blob, key: string): Promise<T> {
    const form = new FormData();
    form.append("front", front, "front.jpg");
    form.append("back", back, "back.jpg");
    return this.request<T>(path, {
      method: "POST",
      body: form,
      headers: { "Idempotency-Key": key },
    });
  }
  capture<T>(draft: CaptureDraft): Promise<T> {
    return this.request<T>("/v1/capture/jobs", {
      method: "POST",
      body: captureForm(draft),
      headers: { "Idempotency-Key": draft.requestId },
    });
  }
  async image(path: string): Promise<Blob> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.request<Blob>(
          path,
          { headers: { Accept: "image/*" } },
          true,
        );
      } catch (error) {
        if (
          !(error instanceof ApiError) ||
          error.status !== 503 ||
          attempt >= 2 ||
          this.closed
        )
          throw error;
        await new Promise((resolve) =>
          setTimeout(resolve, 750 * (attempt + 1)),
        );
      }
    }
  }
  dispose() {
    this.closed = true;
    this.token = "";
    this.libraryListeners.clear();
    for (const c of this.controllers) c.abort();
    this.controllers.clear();
  }
}
