import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { JobsPage } from "../src/JobsPage";
import type { MobileApi } from "../src/api";
import { LEGACY_CAPABILITIES } from "../../../packages/card-core/src/capture";

vi.mock("@capacitor/app", () => ({
  App: { addListener: async () => ({ remove() {} }) },
}));
afterEach(cleanup);

function fixture(status: "unknown" | "conflict") {
  URL.createObjectURL = vi.fn(() => "blob:private-image");
  URL.revokeObjectURL = vi.fn();
  const job = {
    id: "pair",
    status: "succeeded",
    collection_item_id: null,
    front_image_url: "/v1/capture/jobs/pair/images/front",
    back_image_url: "/v1/capture/jobs/pair/images/back",
    created_at: "2026-10-19T00:00:00Z",
    result: {
      confidence: ".9",
      player_name: "Player",
      brand_name: "Topps",
      product_name: "Chrome",
      season_start: 2025,
      pair_validation: {
        status,
        reason_codes: [
          status === "conflict" ? "subject_conflict" : "identity_unverified",
        ],
        evidence: ["两面身份未确认"],
        image_content_hashes: { front: "front", back: "back" },
        version: 1,
      },
    },
  };
  const post = vi.fn(async (_path: string, _payload?: unknown) => ({
    id: "saved",
  }));
  const api = {
    get: vi.fn(async () => [job]),
    image: vi.fn(async () => new Blob(["image"])),
    post,
  } as unknown as MobileApi;
  const onOpen = vi.fn(),
    onCapture = vi.fn();
  render(
    <JobsPage
      api={api}
      user={{
        id: "owner",
        display_name: "Owner",
        email: "owner@example.com",
        base_currency: "CNY",
        role: "member",
      }}
      capabilities={{ ...LEGACY_CAPABILITIES, capture_jobs: true }}
      onOpen={onOpen}
      onCapture={onCapture}
    />,
  );
  return { post, onOpen, onCapture };
}

it("job review displays both current images and saves unknown pairing without claiming human confirmation", async () => {
  const { post, onOpen } = fixture("unknown");
  fireEvent.click(await screen.findByText("Player"));
  await screen.findByAltText("任务正面");
  expect(screen.getByAltText("任务背面")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "确认入库" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith("saved"));
  expect(post.mock.calls[0][0]).toBe("/v1/capture/jobs/pair/confirm");
  expect(post.mock.calls[0][1]).not.toHaveProperty("pair_review");
});

it("conflicting pair jobs offer retaking photos while keeping confirmation blocked", async () => {
  const { post, onCapture } = fixture("conflict");
  fireEvent.click(await screen.findByText("Player"));
  expect(
    screen.getByRole("button", { name: "确认入库" }).hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "重新拍摄 / 选择照片" }));
  expect(onCapture).toHaveBeenCalledOnce();
  expect(post).not.toHaveBeenCalled();
});
