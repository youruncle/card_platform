import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { CapturePage } from "../src/CapturePage";
import { DraftStore } from "../src/drafts";
import type { MobileApi } from "../src/api";
vi.mock("../src/capture", async (original) => ({
  ...(await original<typeof import("../src/capture")>()),
  normalizePhoto: async (b: Blob) => b,
}));
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:photo");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
function setup() {
  const values = new Map();
  const store = new DraftStore("https://test.example", "user", {
    get: async (k) => values.get(k),
    set: async (k, v) => {
      values.set(k, v);
    },
    del: async (k) => {
      values.delete(k);
    },
  });
  const upload = vi.fn().mockResolvedValue({ id: "job" });
  const submitted = vi.fn();
  const api = { upload } as unknown as MobileApi;
  return { store, upload, submitted, api };
}
it("offers phone camera and multi-photo album but no scanner hardware controls", async () => {
  const { store, api, submitted } = setup();
  render(<CapturePage api={api} store={store} onSubmitted={submitted} />);
  await waitFor(() =>
    expect(screen.getByLabelText("拍摄正面照片").hasAttribute("disabled")).toBe(
      false,
    ),
  );
  expect(screen.getByLabelText("拍摄正面照片").getAttribute("capture")).toBe(
    "environment",
  );
  expect(
    screen.getByLabelText("批量选择相册照片").hasAttribute("multiple"),
  ).toBe(true);
  expect(screen.queryByText(/连接扫描仪/)).toBeNull();
});
it("preserves failed uploads and reuses their id; success advances only the matching draft", async () => {
  const { store, api, upload, submitted } = setup();
  const front = new Blob(["front"]),
    back = new Blob(["back"]);
  await store.saveAll([
    { requestId: "one", front, back },
    { requestId: "two", front, back },
  ]);
  upload.mockRejectedValueOnce(new Error("断网"));
  render(<CapturePage api={api} store={store} onSubmitted={submitted} />);
  await screen.findByText("待录入 2 张");
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await screen.findByText("断网");
  expect((await store.loadAll()).map((d) => d.requestId)).toEqual([
    "one",
    "two",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await waitFor(() => expect(store.loadAll()).resolves.toHaveLength(1));
  expect(upload.mock.calls[0][3]).toBe("one");
  expect(upload.mock.calls[1][3]).toBe("one");
  expect((await store.loadAll())[0].requestId).toBe("two");
  expect(submitted).not.toHaveBeenCalled();
});
it("exchanges sides durably and prevents incomplete pair upload", async () => {
  const { store, api, submitted } = setup();
  const front = new Blob(["front"]),
    back = new Blob(["back"]);
  await store.saveAll([{ requestId: "one", front, back }]);
  render(<CapturePage api={api} store={store} onSubmitted={submitted} />);
  fireEvent.click(await screen.findByRole("button", { name: "交换正反面" }));
  await waitFor(async () => expect((await store.load())?.front).toBe(back));
  expect((await store.load())?.requestId).not.toBe("one");
});
it("reconciles an upload completed by a previous page instance before further edits", async () => {
  const { store, api, upload, submitted } = setup();
  const front = new Blob(["front"]),
    back = new Blob(["back"]);
  await store.saveAll([
    { requestId: "one", front, back },
    { requestId: "two", front, back },
  ]);
  let resolve!: (v: unknown) => void;
  upload.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const first = render(
    <CapturePage api={api} store={store} onSubmitted={submitted} />,
  );
  await screen.findByText("待录入 2 张");
  fireEvent.click(screen.getByRole("button", { name: "提交识别" }));
  await waitFor(() => expect(upload).toHaveBeenCalledOnce());
  first.unmount();
  render(<CapturePage api={api} store={store} onSubmitted={submitted} />);
  await screen.findByText("待录入 2 张");
  resolve({ id: "uploaded" });
  await screen.findByText("待录入 1 张");
  fireEvent.click(screen.getByRole("button", { name: "新增一张" }));
  await waitFor(async () => expect(await store.loadAll()).toHaveLength(2));
  expect((await store.loadAll()).some((d) => d.requestId === "one")).toBe(
    false,
  );
});

it("requires explicit single-side choice and submits only the selected side via shared capture", async () => {
  const { store, api, submitted } = setup();
  const capture = vi.fn().mockResolvedValue({ id: "single" });
  Object.assign(api, { capture });
  await store.save({ requestId: "one", back: new Blob(["back"]) });
  const { LEGACY_CAPABILITIES } =
    await import("../../../packages/card-core/src/capture");
  render(
    <CapturePage
      api={api}
      store={store}
      onSubmitted={submitted}
      capabilities={{
        ...LEGACY_CAPABILITIES,
        capture_jobs: true,
        single_side: true,
      }}
    />,
  );
  const submit = await screen.findByRole("button", { name: "提交识别" });
  expect(submit.hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByLabelText("先识别这一面"));
  await waitFor(() => expect(submit.hasAttribute("disabled")).toBe(false));
  fireEvent.click(submit);
  await waitFor(() => expect(capture).toHaveBeenCalledOnce());
  expect(capture.mock.calls[0][0].front).toBeUndefined();
  expect(capture.mock.calls[0][0].back).toBeTruthy();
  await waitFor(() => expect(submitted).toHaveBeenCalledOnce());
});
it("supplement drafts only offer the missing side and retain target after reload", async () => {
  const { store, api, submitted } = setup();
  await store.save({
    requestId: "supp",
    targetItemId: "existing",
    expectedRevision: "v1",
    missingSide: "back",
    singleSide: true,
  });
  render(<CapturePage api={api} store={store} onSubmitted={submitted} />);
  await screen.findByText("补充卡片照片");
  expect(screen.queryByLabelText("拍摄正面照片")).toBeNull();
  expect(screen.getByLabelText("拍摄背面照片")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "交换正反面" }).hasAttribute("disabled"),
  ).toBe(true);
});
