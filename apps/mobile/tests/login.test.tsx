import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../src/App";
import { sessionVault } from "../src/session";
import { MobileApi } from "../src/api";
import type { Session } from "../src/types";
const user: Session["user"] = { id: "owner", email: "owner@example.com", display_name: "Owner", role: "member", base_currency: "CNY" };
const stored = (origin: string): Session => ({ origin, token: "private-token", expires_at: "2099-01-01T00:00:00Z", user });
const json = (body: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify(body), { status, headers });
beforeEach(async () => { await sessionVault.clear(); });
afterEach(async () => { cleanup(); await sessionVault.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
async function loginForm() {
  render(<App />);
  await screen.findByRole("heading", { name: "登录你的卡库" });
  fireEvent.change(screen.getByLabelText(/用户名|邮箱/), { target: { value: "owner@example.com" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
  return screen.getByRole("button", { name: "登录" });
}
it("sends only username and password to the fixed cloud despite an old origin environment setting", async () => {
  vi.stubEnv("VITE_API_ORIGIN", "https://old.example");
  const fetcher = vi.fn(async (_url: RequestInfo | URL, _options?: RequestInit) => json({ detail: "邮箱或密码错误，请核对后重试。" }, 401));
  vi.stubGlobal("fetch", fetcher);
  const submit = await loginForm();
  expect(screen.queryByLabelText("服务地址")).toBeNull();
  const username = screen.getByLabelText(/用户名|邮箱/) as HTMLInputElement;
  expect(username.autocomplete).toBe("username");
  expect(username.name).toBe("username");
  expect(username.getAttribute("autocapitalize")).toBe("none");
  const password = screen.getByLabelText("密码") as HTMLInputElement;
  expect(password.autocomplete).toBe("current-password");
  fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
  expect(password.type).toBe("text");
  fireEvent.click(screen.getByRole("button", { name: "隐藏密码" }));
  expect(password.type).toBe("password");
  fireEvent.click(submit);
  await screen.findByRole("alert");
  expect(fetcher.mock.calls[0]?.[0]).toBe("https://cardsp.cn/v1/mobile/auth/login");
  expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({ email: "owner@example.com", password: "password" });
  expect(screen.getByRole("alert").textContent).toMatch(/邮箱或密码错误/);
});
it("never forwards or restores a session saved for another origin", async () => {
  await sessionVault.save(stored("https://1.117.65.47"));
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await screen.findByRole("heading", { name: "登录你的卡库" });
  expect(fetcher).not.toHaveBeenCalled();
  expect(await sessionVault.load()).toBeNull();
  expect(screen.getByRole("alert").textContent).toMatch(/重新登录/);
  expect(screen.getByRole("alert").textContent).toContain("cardsp.cn");
});
it("preserves and validates a session already saved for the fixed cloud", async () => {
  await sessionVault.save(stored("https://cardsp.cn"));
  const fetcher = vi.fn(async (url: string) => url.endsWith("/v1/auth/me") ? json(user) : json({ detail: "unavailable" }, 503));
  vi.stubGlobal("fetch", fetcher); render(<App />);
  await screen.findByRole("navigation", { name: "主导航" });
  expect(fetcher.mock.calls[0]?.[0]).toBe("https://cardsp.cn/v1/auth/me");
  expect(await sessionVault.load()).toMatchObject({ token: "private-token", origin: "https://cardsp.cn" });
});
it("shows an expired session message and clears rejected stored credentials", async () => {
  await sessionVault.save(stored("https://cardsp.cn"));
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Invalid token" }, 401)));
  render(<App />); await screen.findByRole("heading", { name: "登录你的卡库" });
  expect(screen.getByRole("alert").textContent).toMatch(/登录已过期/);
  expect(await sessionVault.load()).toBeNull();
});
it("blocks duplicate submits while login is pending and saves a successful cloud session", async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn((url: string) => url.endsWith("/login") ? new Promise<Response>((resolve) => { finish = resolve; }) : Promise.resolve(json({ detail: "unavailable" }, 503)));
  vi.stubGlobal("fetch", fetcher);
  const submit = await loginForm();
  fireEvent.submit(submit.closest("form")!); fireEvent.submit(submit.closest("form")!);
  expect(fetcher).toHaveBeenCalledOnce(); expect((submit as HTMLButtonElement).disabled).toBe(true);
  await act(async () => finish(json(stored("https://cardsp.cn"))));
  await screen.findByRole("navigation", { name: "主导航" });
  expect(await sessionVault.load()).toMatchObject({ token: "private-token", origin: "https://cardsp.cn" });
});
it("shows remaining lock time and prevents retries until the server deadline", async () => {
  const fetcher = vi.fn(async () => json({ detail: { code: "account_temporarily_locked", message: "登录尝试过多，请稍后重试。", retry_after_seconds: 121 } }, 429, { "Retry-After": "121" }));
  vi.stubGlobal("fetch", fetcher); const submit = await loginForm(); vi.useFakeTimers();
  await act(async () => fireEvent.click(submit));
  expect(screen.getByRole("alert").textContent).toMatch(/2\s*分/);
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(submit.closest("form")!); expect(fetcher).toHaveBeenCalledOnce();
  await act(async () => vi.advanceTimersByTime(120000)); expect((submit as HTMLButtonElement).disabled).toBe(true);
  await act(async () => vi.advanceTimersByTime(1000)); expect((submit as HTMLButtonElement).disabled).toBe(false);
  await act(async () => fireEvent.click(submit)); expect(fetcher).toHaveBeenCalledTimes(2);
});
it.each([
  [new TypeError("Failed to fetch"), /网络/],
  [new Error("SSL certificate validation failed"), /安全连接|证书/],
  [new DOMException("Aborted", "AbortError"), /超时/],
])("provides useful login connection errors without capture draft wording: %s", async (failure, message) => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw failure; })); const submit = await loginForm(); fireEvent.click(submit);
  const alert = await screen.findByRole("alert"); expect(alert.textContent).toMatch(message); expect(alert.textContent).not.toMatch(/草稿|服务器地址/);
  expect((submit as HTMLButtonElement).disabled).toBe(false);
});
it("preserves structured lock metadata and supports legacy string details", async () => {
  const locked = new MobileApi("https://cardsp.cn", "", () => {}, async () => json({ detail: { code: "account_temporarily_locked", message: "稍后重试", retry_after_seconds: 61 } }, 429));
  await expect(locked.post("/v1/mobile/auth/login", {})).rejects.toMatchObject({ status: 429, message: "稍后重试", code: "account_temporarily_locked", retryAfter: 61 });
  const legacy = new MobileApi("https://cardsp.cn", "", () => {}, async () => json({ detail: "邮箱或密码错误" }, 401));
  await expect(legacy.post("/v1/mobile/auth/login", {})).rejects.toMatchObject({ status: 401, message: "邮箱或密码错误" });
});
it("uses Retry-After if a locking server only supplies the header", async () => {
  const api = new MobileApi("https://cardsp.cn", "", () => {}, async () => json({ detail: { code: "account_temporarily_locked", message: "稍后重试" } }, 429, { "Retry-After": "42" }));
  await expect(api.post("/v1/mobile/auth/login", {})).rejects.toMatchObject({ retryAfter: 42 });
});
it.each([
  [true, false, "http://127.0.0.1:5178", "https://cardsp.cn"],
  [false, true, "https://foreign.example", "https://cardsp.cn"],
  [false, true, "http://127.0.0.1:5178", "http://127.0.0.1:5178"],
])("restricts explicit developer destinations: prod=%s dev=%s origin=%s", async (production, development, override, expected) => {
  vi.stubEnv("PROD", production); vi.stubEnv("DEV", development); vi.stubEnv("VITE_MOBILE_DEV_ORIGIN", override);
  const fetcher = vi.fn(async (_url: RequestInfo | URL) => json({ detail: "邮箱或密码错误" }, 401)); vi.stubGlobal("fetch", fetcher);
  fireEvent.click(await loginForm()); await screen.findByRole("alert");
  expect(fetcher.mock.calls[0]?.[0]).toBe(`${expected}/v1/mobile/auth/login`);
});
it("scopes lock deadlines to normalized accounts while allowing another account to submit", async () => {
  const fetcher = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => {
    const { email } = JSON.parse(String(options?.body));
    return json({ detail: {
      code: "account_temporarily_locked",
      message: "登录尝试过多，请稍后重试。",
      retry_after_seconds: email === "owner@example.com" ? 121 : 61,
    } }, 429);
  });
  vi.stubGlobal("fetch", fetcher);
  const submit = await loginForm();
  const username = screen.getByLabelText("用户名（邮箱）");
  vi.useFakeTimers();
  await act(async () => fireEvent.click(submit));
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(username, { target: { value: "other@example.com" } });
  expect((submit as HTMLButtonElement).disabled).toBe(false);
  await act(async () => fireEvent.submit(submit.closest("form")!));
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body)).email).toBe("other@example.com");
  expect(screen.getByRole("alert").textContent).toMatch(/1 分 1 秒/);
  fireEvent.change(username, { target: { value: " OWNER@EXAMPLE.COM " } });
  expect(screen.getByRole("alert").textContent).toMatch(/2 分 1 秒/);
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(submit.closest("form")!);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTime(61000));
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(username, { target: { value: "OTHER@example.com" } });
  expect((submit as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(username, { target: { value: "owner@example.com" } });
  expect(screen.getByRole("alert").textContent).toMatch(/1 分 0 秒/);
  expect((submit as HTMLButtonElement).disabled).toBe(true);
});
