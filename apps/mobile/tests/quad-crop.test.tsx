import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { CapturePage } from "../src/CapturePage";
import { DraftStore, type Draft } from "../src/drafts";
import type { MobileApi } from "../src/api";
import {
  LEGACY_CAPABILITIES,
  type Quad,
} from "../../../packages/card-core/src/capture";

// Browser raster encoding is a boundary in jsdom. Keep real edit composition,
// editor state and persistence; label encoded pixels to detect lost operations.
vi.mock("../src/capture", async (original) => ({
  ...(await original<typeof import("../src/capture")>()),
  normalizePhoto: async (
    blob: Blob,
    rotation = 0,
    crop?: { x: number; y: number; width: number; height: number },
  ) =>
    new Blob(
      [
        blob,
        crop ? `:rect-${crop.x},${crop.y},${crop.width},${crop.height}` : "",
        `:rotated-${rotation}`,
      ],
      { type: "image/jpeg" },
    ),
}));
function contents(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsText(blob);
  });
}
const quad: Quad = [
  [0.25, 0.25],
  [0.75, 0.25],
  [0.75, 0.75],
  [0.25, 0.75],
];
const capabilities = {
  ...LEGACY_CAPABILITIES,
  capture_jobs: true,
  crop_proposals: true,
  single_side: true,
};
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:crop-test");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
async function setup(overrides: Partial<Draft> = {}) {
  const values = new Map<string, unknown>();
  const store = new DraftStore("https://crop.example", "owner", {
    get: async (key) => values.get(key),
    set: async (key, value) => {
      values.set(key, value);
    },
    del: async (key) => {
      values.delete(key);
    },
  });
  const source = new Blob(["original uncropped source"], {
    type: "image/jpeg",
  });
  const cropped = new Blob(["perspective-cropped pixels"], {
    type: "image/jpeg",
  });
  await store.save({
    requestId: "initial",
    front: source,
    frontSource: source,
    back: new Blob(["back"]),
    recipes: { back: { confirmed: true } },
    ...overrides,
  });
  const proposal = {
    proposal_id: "proposal",
    source_hash: "hash",
    source_url: "/v1/source",
    preview_url: "/v1/preview",
    quad,
    width: 100,
    height: 150,
    confidence: 0.9,
  };
  const request = vi.fn(
    async (_path: string, _options?: RequestInit) => proposal,
  );
  const post = vi.fn(async (_path: string, _body?: unknown) => proposal);
  const image = vi.fn(async () => cropped);
  const capture = vi.fn(async (_draft: Draft) => ({ id: "job" }));
  const api = { request, post, image, capture } as unknown as MobileApi;
  const submitted = vi.fn();
  return {
    store,
    source,
    cropped,
    proposal,
    request,
    post,
    image,
    capture,
    api,
    submitted,
  };
}

it("blocks submit and parent edits during delayed crop confirmation, then uploads confirmed pixels", async () => {
  const f = await setup();
  let resolve!: (value: typeof f.proposal) => void;
  f.post.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={capabilities}
    />,
  );
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await screen.findByRole("button", { name: "确认裁切" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "确认裁切" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认裁切" }));
  for (const name of ["提交识别", "交换正反面", "移除此草稿", "新增一张"]) {
    const button = screen.getByRole("button", { name });
    expect(button.hasAttribute("disabled")).toBe(true);
    fireEvent.click(button);
  }
  expect(f.capture).not.toHaveBeenCalled();
  expect((await f.store.load())?.front).toBe(f.source);
  await act(async () => {
    resolve(f.proposal);
  });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "确认裁切" })).toBeNull(),
  );
  const updated = await f.store.load();
  expect(updated?.front).toBe(f.cropped);
  expect(updated?.frontSource).toBe(f.source);
  expect(updated?.recipes?.front).toMatchObject({ quad, confirmed: true });
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await waitFor(() => expect(f.submitted).toHaveBeenCalledOnce());
  expect(f.capture.mock.calls[0][0].front).toBe(f.cropped);
});

it("crop then repeated rotation rebuilds both edits from the same immutable source", async () => {
  const f = await setup({
    recipes: { front: { quad, confirmed: true }, back: { confirmed: true } },
  });
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={capabilities}
    />,
  );
  await screen.findByText("待录入 1 张");
  for (const angle of [90, 180]) {
    fireEvent.click(screen.getAllByRole("button", { name: "旋转" })[0]);
    await waitFor(async () =>
      expect((await f.store.load())?.recipes?.front?.rotation).toBe(angle),
    );
    const draft = await f.store.load();
    expect(await contents(draft!.front!)).toBe(
      `perspective-cropped pixels:rotated-${angle}`,
    );
    expect(draft?.frontSource).toBe(f.source);
    expect(draft?.recipes?.front?.quad).toEqual(quad);
  }
  for (const [, options] of f.request.mock.calls) {
    expect(
      await contents((options!.body as FormData).get("image") as Blob),
    ).toBe("original uncropped source");
  }
});

it("rotation survives a later quad crop", async () => {
  const f = await setup({
    recipes: {
      front: { rotation: 90, confirmed: true },
      back: { confirmed: true },
    },
  });
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={capabilities}
    />,
  );
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getByRole("button", { name: "裁切正面" }));
  await screen.findByText(/结果将保留 90° 旋转/);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "确认裁切" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认裁切" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "确认裁切" })).toBeNull(),
  );
  const draft = await f.store.load();
  expect(await contents(draft!.front!)).toBe(
    "perspective-cropped pixels:rotated-90",
  );
  expect(draft?.recipes?.front).toMatchObject({
    quad,
    rotation: 90,
    confirmed: true,
  });
  expect(draft?.frontSource).toBe(f.source);
});

it("legacy rectangular crop can still rotate without the unavailable proposal endpoint", async () => {
  const f = await setup({
    recipes: { front: { quad, confirmed: true }, back: { confirmed: true } },
  });
  f.request.mockRejectedValue(new Error("旧服务器没有裁切提议接口"));
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={LEGACY_CAPABILITIES}
    />,
  );
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getAllByRole("button", { name: "旋转" })[0]);
  await waitFor(async () =>
    expect((await f.store.load())?.recipes?.front?.rotation).toBe(90),
  );
  expect(f.request).not.toHaveBeenCalled();
  expect(await contents((await f.store.load())!.front!)).toBe(
    "original uncropped source:rect-0.25,0.25,0.5,0.5:rotated-90",
  );
  expect((await f.store.load())?.recipes?.front?.quad).toEqual(quad);
  expect((await f.store.load())?.frontSource).toBe(f.source);
});

it("legacy local crop also preserves an existing rotation", async () => {
  const f = await setup({
    recipes: {
      front: { rotation: 90, confirmed: true },
      back: { confirmed: true },
    },
  });
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={LEGACY_CAPABILITIES}
    />,
  );
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getByRole("button", { name: "裁切正面" }));
  for (const label of ["左边", "右边", "上边", "下边"])
    fireEvent.change(screen.getByLabelText(new RegExp(label)), {
      target: { value: "25" },
    });
  fireEvent.click(screen.getByRole("button", { name: "应用裁切" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "应用裁切" })).toBeNull(),
  );
  const draft = await f.store.load();
  expect(draft?.recipes?.front).toMatchObject({ quad, rotation: 90 });
  expect(await contents(draft!.front!)).toBe(
    "original uncropped source:rect-0.25,0.25,0.5,0.5:rotated-90",
  );
  expect(f.request).not.toHaveBeenCalled();
});

it("keeps the confirmed first-side recipe when a replacement second photo is confirmed", async () => {
  const f = await setup();
  render(
    <CapturePage
      api={f.api}
      store={f.store}
      onSubmitted={f.submitted}
      capabilities={capabilities}
    />,
  );
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getByRole("button", { name: "裁切正面" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "保留整张" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "保留整张" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "保留整张" })).toBeNull(),
  );
  const first = await f.store.load();
  expect(first?.recipes?.front?.confirmed).toBe(true);
  fireEvent.change(screen.getByLabelText("选择背面照片"), {
    target: {
      files: [
        new File(["replacement back"], "back.jpg", { type: "image/jpeg" }),
      ],
    },
  });
  await screen.findByRole("button", { name: "保留整张" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "保留整张" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "保留整张" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "保留整张" })).toBeNull(),
  );
  const final = await f.store.load();
  expect(final?.recipes?.front).toEqual(first?.recipes?.front);
  expect(final?.frontSource).toBe(first?.frontSource);
  expect(final?.recipes?.back?.confirmed).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await waitFor(() => expect(f.submitted).toHaveBeenCalledOnce());
  expect(f.capture.mock.calls[0][0].recipes).toEqual(final?.recipes);
});
