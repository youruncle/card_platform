import { describe, it, expect, vi } from "vitest";
import { MobileApi, ApiError, validateOrigin, protectedUrl } from "../src/api";
import { confirmationPayload } from "../src/confirmation";
import { DraftStore } from "../src/drafts";

describe("credential destinations", () => {
  it("rejects cleartext public servers, userinfo and paths", () => {
    for (const url of [
      "http://1.117.65.47",
      "https://u:p@example.com",
      "https://example.com/path",
      "https://example.com?x=1",
    ])
      expect(() => validateOrigin(url)).toThrow();
    expect(validateOrigin("https://cards.example.com/")).toBe(
      "https://cards.example.com",
    );
    expect(validateOrigin("http://127.0.0.1:5178", true)).toBe(
      "http://127.0.0.1:5178",
    );
    expect(() => validateOrigin("http://127.0.0.1:5178")).toThrow();
  });
  it("never sends credentials to foreign images or protocol-relative URLs", () => {
    for (const path of [
      "https://evil.example/uploads/x",
      "//evil.example/x",
      "/\\evil.example/x",
      "/v1/../outside",
    ])
      expect(() => protectedUrl("https://cards.example.com", path)).toThrow();
    expect(
      protectedUrl("https://cards.example.com", "/uploads/a.jpg?v=1"),
    ).toBe("https://cards.example.com/uploads/a.jpg?v=1");
  });
  it("invalidates the session on 401 and aborts stale requests", async () => {
    const invalidated = vi.fn();
    let seen: RequestInit | undefined;
    const api = new MobileApi(
      "https://cards.example.com",
      "private",
      invalidated,
      async (_url, options) => {
        seen = options;
        return new Response(JSON.stringify({ detail: "expired" }), {
          status: 401,
        });
      },
    );
    await expect(api.get("/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(invalidated).toHaveBeenCalledOnce();
    expect(new Headers(seen?.headers).get("Authorization")).toBe(
      "Bearer private",
    );
    expect(seen?.credentials).toBe("omit");
    api.dispose();
    await expect(api.get("/v1/auth/me")).rejects.toThrow();
  });
});

describe("human confirmation", () => {
  it("preserves blank identity as null and saves supplied facts without inventing unknown season or player", () => {
    expect(
      confirmationPayload("owner", { confidence: "0.7" }, {}).card,
    ).toMatchObject({
      player_name: null,
      brand_name: null,
      product_name: null,
      season_start: null,
    });
    const result = confirmationPayload(
      "owner",
      { confidence: "0.7", is_autograph: true, serial_total: 99 },
      {
        player_name: "Test Player",
        brand_name: "Test",
        product_name: "Test",
        season_start: "2024",
      },
    );
    expect(result.card.season_start).toBe(2024);
    expect(result.card.is_autograph).toBe(true);
    expect(result.serial_total).toBe(99);
    expect(result.card.autograph_type).toBe("unknown");
    expect(result).not.toHaveProperty("purchase");
  });
});

describe("durable account-scoped drafts", () => {
  it("retains the same request id across reload and isolates accounts and origins", async () => {
    const storage = new Map<string, unknown>();
    const backend = {
      get: async (key: string) => storage.get(key),
      set: async (key: string, value: unknown) => {
        storage.set(key, value);
      },
      del: async (key: string) => {
        storage.delete(key);
      },
    };
    const a = new DraftStore("https://a.example", "owner-a", backend);
    const draft = {
      requestId: "request-1",
      front: new Blob(["front"]),
      back: new Blob(["back"]),
    };
    await a.save(draft);
    expect(
      (await new DraftStore("https://a.example", "owner-a", backend).load())
        ?.requestId,
    ).toBe("request-1");
    expect(
      await new DraftStore("https://a.example", "owner-b", backend).load(),
    ).toBeUndefined();
    expect(
      await new DraftStore("https://b.example", "owner-a", backend).load(),
    ).toBeUndefined();
    await a.clear();
    expect(await a.load()).toBeUndefined();
  });
});

it("an old upload cannot clear a newer draft, and disposed stores reject late writes", async () => {
  const data = new Map<string, unknown>();
  const storage = {
    get: async (key: string) => data.get(key),
    set: async (key: string, value: unknown) => {
      data.set(key, value);
    },
    del: async (key: string) => {
      data.delete(key);
    },
  };
  const store = new DraftStore("https://cards.example", "owner", storage);
  await store.save({ requestId: "newer", front: new Blob(["new"]) });
  await store.clearIfRequestId("older");
  expect((await store.load())?.requestId).toBe("newer");
  await store.clearIfRequestId("newer");
  expect(await store.load()).toBeUndefined();
  store.dispose();
  await expect(store.save({ requestId: "late" })).rejects.toThrow();
  expect(await store.load()).toBeUndefined();
});

it("successful writes invalidate only their API session and disposed sessions reject late responses", async () => {
  let release: ((value: Response) => void) | undefined;
  const a = new MobileApi(
    "https://a.example",
    "account-a",
    () => {},
    async () => new Response(null, { status: 204 }),
  );
  const b = new MobileApi(
    "https://a.example",
    "account-b",
    () => {},
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const changedA = vi.fn(),
    changedB = vi.fn();
  a.subscribeLibrary(changedA);
  b.subscribeLibrary(changedB);
  await a.post("/v1/portfolio/items");
  expect(changedA).toHaveBeenCalledOnce();
  expect(changedB).not.toHaveBeenCalled();
  const pending = b.get("/v1/portfolio/items/page");
  b.dispose();
  release!(new Response(JSON.stringify({ items: [] })));
  await expect(pending).rejects.toThrow("会话已结束");
  await expect(b.post("/v1/portfolio/items")).rejects.toThrow("会话已结束");
  a.dispose();
});
