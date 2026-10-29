import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { JobsPage } from "../src/JobsPage";
import { ApiError, type MobileApi } from "../src/api";
import { LEGACY_CAPABILITIES } from "../../../packages/card-core/src/capture";

vi.mock("@capacitor/app", () => ({
  App: { addListener: async () => ({ remove() {} }) },
}));
vi.mock("../src/capture", async (original) => ({
  ...(await original<typeof import("../src/capture")>()),
  normalizePhoto: async (blob: Blob) => blob,
}));
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:task-photo");
  URL.revokeObjectURL = vi.fn();
});
afterEach(cleanup);
const user = {
  id: "owner",
  display_name: "Owner",
  email: "owner@example.com",
  base_currency: "CNY",
  role: "member" as const,
};
const capabilities = { ...LEGACY_CAPABILITIES, capture_jobs: true };
function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "scan",
    status: "failed",
    collection_item_id: null,
    error_message: "识别服务超时，请核对照片或重试",
    attempt_count: 3,
    front_image_url: "/v1/capture/jobs/scan/images/front",
    back_image_url: "/v1/capture/jobs/scan/images/back",
    created_at: "2026-10-19T00:00:00Z",
    available_sides: ["front", "back"],
    source: "scanner",
    require_confirmation: false,
    revision: 1,
    images: {},
    target_item_id: null,
    result: null,
    display_status: "failed",
    attention_reasons: ["识别服务超时，请核对照片或重试"],
    can_retry: true,
    image_content_hashes: { front: "current-front", back: "current-back" },
    replacement_job_id: null,
    history: [
      {
        id: "failure",
        occurred_at: "2026-10-19T00:01:00Z",
        attempt_count: 3,
        display_status: "failed",
        message: "第3次识别超时",
        source: "worker",
      },
    ],
    ...overrides,
  };
}
function page(items: ReturnType<typeof task>[], overrides = {}) {
  return {
    items,
    counts: {
      processing: 0,
      needs_review: 0,
      failed: 1,
      imported: 0,
      cancelled: 0,
      total: 1,
    },
    total: items.length,
    page: 1,
    page_size: 20,
    total_pages: 1,
    ...overrides,
  };
}
function setup(
  get: (path: string) => Promise<unknown>,
  post = vi.fn(async () => ({ id: "saved" })),
  upload = vi.fn(async () =>
    task({
      id: "replacement",
      status: "queued",
      display_status: "processing",
      history: [],
    }),
  ),
) {
  const api = {
    get: vi.fn(get),
    image: vi.fn(async () => new Blob(["image"])),
    post,
    upload,
  } as unknown as MobileApi;
  const onOpen = vi.fn();
  render(
    <JobsPage
      api={api}
      user={user}
      capabilities={capabilities}
      onOpen={onOpen}
    />,
  );
  return { api, post, upload, onOpen };
}

it("shows automatic scanner failures, pages beyond 100, and permits a new manual attempt after three failures", async () => {
  const failed = task();
  const { api, post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? failed
      : page([failed], { total: 121, total_pages: 7 }),
  );
  await screen.findByText("识别服务超时，请核对照片或重试");
  expect(api.get).toHaveBeenCalledWith(
    "/v1/capture/tasks?state=attention&page=1&page_size=20",
  );
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await waitFor(() =>
    expect(api.get).toHaveBeenCalledWith(
      "/v1/capture/tasks?state=attention&page=2&page_size=20",
    ),
  );
  fireEvent.change(screen.getByLabelText("任务状态"), {
    target: { value: "failed" },
  });
  await waitFor(() =>
    expect(api.get).toHaveBeenCalledWith(
      "/v1/capture/tasks?state=failed&page=1&page_size=20",
    ),
  );
  fireEvent.click(screen.getByText("新的录卡任务"));
  await screen.findByText("第3次识别超时");
  expect(await screen.findByAltText("任务正面")).toBeTruthy();
  expect(screen.getByAltText("任务背面")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "重新识别" }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "重新识别" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/v1/capture/tasks/scan/retry"),
  );
});

it("allows a failed task without a recognition result to save partial identity without claiming human pair review", async () => {
  const failed = task();
  const { post, onOpen } = setup(async (path) =>
    path === "/v1/capture/tasks/scan" ? failed : page([failed]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  fireEvent.change(await screen.findByLabelText("球员"), {
    target: { value: "Player" },
  });
  fireEvent.change(screen.getByLabelText("品牌"), {
    target: { value: "Topps" },
  });
  fireEvent.change(screen.getByLabelText("产品"), {
    target: { value: "Chrome" },
  });
  fireEvent.change(screen.getByLabelText("赛季年份"), {
    target: { value: "2025" },
  });
  expect(
    screen.getByRole("button", { name: "确认入库" }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith("saved"));
  expect(post).toHaveBeenCalledWith(
    "/v1/capture/tasks/scan/confirm",
    expect.objectContaining({
      card: expect.objectContaining({
        player_name: "Player",
        season_start: 2025,
      }),
    }),
  );
});

it("preserves previous failures in imported history and opens the existing card", async () => {
  const imported = task({
    status: "succeeded",
    display_status: "imported",
    error_message: null,
    attention_reasons: [],
    collection_item_id: "existing",
    can_retry: false,
    history: [
      ...task().history,
      {
        id: "recovery",
        occurred_at: "2026-10-19T00:02:00Z",
        attempt_count: 4,
        display_status: "imported",
        message: "人工确认已入库",
        source: "manual",
      },
    ],
  });
  const { onOpen } = setup(async (path) =>
    path === "/v1/capture/tasks/scan" ? imported : page([imported]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  await screen.findByText("第3次识别超时");
  expect(screen.getByText("人工确认已入库")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看卡片" }));
  expect(onOpen).toHaveBeenCalledWith("existing");
  expect(screen.queryByRole("button", { name: "确认入库" })).toBeNull();
});

it("blocks a true conflict and replaces its images with an idempotent linked task", async () => {
  const conflict = task({
    status: "succeeded",
    display_status: "needs_review",
    result: {
      confidence: ".8",
      player_name: "Player",
      pair_validation: {
        status: "conflict",
        reason_codes: ["subject_conflict"],
        evidence: [],
        image_content_hashes: { front: "current-front", back: "current-back" },
        version: 1,
      },
    },
  });
  const { post, upload } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? conflict
      : path === "/v1/capture/tasks/replacement"
        ? task({ id: "replacement", display_status: "processing" })
        : page([conflict]),
  );
  fireEvent.click(await screen.findByText("Player"));
  expect(
    (await screen.findByRole("button", { name: "确认入库" })).hasAttribute(
      "disabled",
    ),
  ).toBe(true);
  expect(
    screen.queryByLabelText("我已查看当前正反面，确认属于同一张卡"),
  ).toBeNull();
  const front = new File(["new-front"], "front.jpg", { type: "image/jpeg" });
  const back = new File(["new-back"], "back.jpg", { type: "image/jpeg" });
  fireEvent.change(screen.getByLabelText("更换正面照片"), {
    target: { files: [front] },
  });
  await screen.findByAltText("替换正面预览");
  fireEvent.change(screen.getByLabelText("更换背面照片"), {
    target: { files: [back] },
  });
  await screen.findByAltText("替换背面预览");
  fireEvent.click(screen.getByRole("button", { name: "更换照片并重新识别" }));
  await waitFor(() =>
    expect(upload).toHaveBeenCalledWith(
      "/v1/capture/tasks/scan/images",
      front,
      back,
      expect.stringMatching(/^[\da-f-]{36}$/i),
    ),
  );
  expect(post).not.toHaveBeenCalled();
  await screen.findByText("照片已更换，新任务保留与原任务的关联。");
});

it("preserves unknown attributes until a user explicitly chooses yes or no", async () => {
  const failed = task();
  const { post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan" ? failed : page([failed]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  const rookie = (await screen.findByRole("combobox", {
    name: "新秀卡",
  })) as HTMLSelectElement;
  expect(rookie.value).toBe("");
  fireEvent.change(rookie, { target: { value: "false" } });
  fireEvent.change(screen.getByLabelText("球员"), {
    target: { value: "Player" },
  });
  fireEvent.change(screen.getByLabelText("品牌"), {
    target: { value: "Topps" },
  });
  fireEvent.change(screen.getByLabelText("产品"), {
    target: { value: "Chrome" },
  });
  fireEvent.change(screen.getByLabelText("赛季年份"), {
    target: { value: "2025" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/v1/capture/tasks/scan/confirm",
      expect.objectContaining({
        card: expect.objectContaining({
          is_rookie: false,
          is_autograph: null,
          is_memorabilia: null,
          is_patch: null,
        }),
      }),
    ),
  );
});

it("confirms a supplemental task through the unified endpoint without changing identity", async () => {
  const supplement = task({
    status: "succeeded",
    display_status: "needs_review",
    target_item_id: "original",
    result: {
      confidence: ".9",
      pair_validation: {
        status: "consistent",
        reason_codes: [],
        evidence: [],
        image_content_hashes: { front: "current-front", back: "current-back" },
        version: 1,
      },
    },
  });
  const { post, onOpen } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? supplement
      : path === "/v1/capture/items/original"
        ? { identity: {}, can_accept_identity: false }
        : page([supplement]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  fireEvent.click(
    await screen.findByRole("button", { name: "确认补充到原卡片" }),
  );
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith("saved"));
  expect(post).toHaveBeenCalledWith("/v1/capture/tasks/scan/confirm", {});
});

it("saves an unknown supplemental pair without inventing a human review", async () => {
  const supplement = task({
    status: "succeeded",
    display_status: "needs_review",
    target_item_id: "original",
    result: {
      confidence: ".9",
      pair_validation: {
        status: "unknown",
        reason_codes: ["identity_unverified"],
        evidence: [],
        image_content_hashes: { front: "current-front", back: "current-back" },
        version: 1,
      },
    },
  });
  const { post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? supplement
      : path === "/v1/capture/items/original"
        ? { identity: {}, can_accept_identity: false }
        : page([supplement]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  expect(
    (
      await screen.findByRole("button", { name: "确认补充到原卡片" })
    ).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "确认补充到原卡片" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/v1/capture/tasks/scan/confirm", {}),
  );
});

it("recovers a failed supplemental task using its current photos while retaining the original card identity", async () => {
  const supplement = task({ target_item_id: "original" });
  const { post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? supplement
      : path === "/v1/capture/items/original"
        ? { identity: {}, can_accept_identity: false }
        : page([supplement]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  await screen.findByRole("button", { name: "确认补充到原卡片" });
  expect(screen.queryByLabelText("球员")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "确认补充到原卡片" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/v1/capture/tasks/scan/confirm", {}),
  );
});

it("offers current-photo review for an older task whose recognition result lacks pair validation", async () => {
  const old = task({
    status: "succeeded",
    display_status: "needs_review",
    result: { confidence: ".7", player_name: "Older Player" },
  });
  setup(async (path) =>
    path === "/v1/capture/tasks/scan" ? old : page([old]),
  );
  fireEvent.click(await screen.findByText("Older Player"));
  await screen.findByText(/正反面配对待确认/);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "确认入库" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("blocks a supplemental task with a definite pair conflict", async () => {
  const supplement = task({
    status: "succeeded",
    display_status: "needs_review",
    target_item_id: "original",
    result: {
      confidence: ".8",
      pair_validation: {
        status: "conflict",
        reason_codes: ["subject_conflict"],
        evidence: [],
        image_content_hashes: { front: "current-front", back: "current-back" },
        version: 1,
      },
    },
  });
  const { post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan"
      ? supplement
      : path === "/v1/capture/items/original"
        ? { identity: {}, can_accept_identity: false }
        : page([supplement]),
  );
  fireEvent.click(await screen.findByText("新的录卡任务"));
  await screen.findByText(/正反面存在明确冲突/);
  expect(screen.queryByRole("button", { name: "确认补充到原卡片" })).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

it("shows a request failure without presenting it as an empty task list", async () => {
  setup(async () => {
    throw new Error("云端任务暂不可用");
  });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "云端任务暂不可用",
  );
  expect(screen.queryByText("还没有录卡任务")).toBeNull();
});

it("does not prefill or save unknown and conflicted model guesses from a task", async () => {
  const review = task({
    status: "succeeded",
    display_status: "needs_review",
    result: {
      confidence: 0.6,
      player_name: "Known Player",
      product_name: "Unverified Chrome",
      season_start: 2025,
      field_assessments: {
        product_name: { status: "conflicted" },
        season_start: { status: "unknown" },
      },
    },
  });
  const { post } = setup(async (path) =>
    path === "/v1/capture/tasks/scan" ? review : page([review]),
  );
  fireEvent.click(await screen.findByText("Known Player"));
  expect(
    ((await screen.findByLabelText("赛季年份")) as HTMLInputElement).value,
  ).toBe("");
  expect((screen.getByLabelText("产品") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/v1/capture/tasks/scan/confirm",
      expect.objectContaining({
        card: expect.objectContaining({
          player_name: "Known Player",
          product_name: null,
          season_start: null,
        }),
      }),
    ),
  );
});

it("falls back to legacy jobs only when the task API is unavailable", async () => {
  const { api } = setup(async (path) => {
    if (path.startsWith("/v1/capture/tasks"))
      throw new ApiError(404, "Not Found");
    return [task()];
  });
  await screen.findByText("新的录卡任务");
  expect(api.get).toHaveBeenCalledWith("/v1/capture/jobs");
});
