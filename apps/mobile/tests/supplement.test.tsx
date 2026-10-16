import React from "react";
import { it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from "@testing-library/react";
import { SupplementConfirmation } from "../src/SupplementConfirmation";
import type { MobileApi } from "../src/api";
import type { Job } from "../src/types";
afterEach(cleanup);
const identity = {
  player_name: "Original Player",
  brand_name: "Original Brand",
  season_start: 2024,
  season_end: 2025,
  product_name: "Original Series",
  is_autograph: true,
  autograph_type: "on_card",
  has_inscription: true,
  inscription_text: "Keep me",
  print_run: 99,
};
const job = {
  id: "job",
  target_item_id: "card",
  result: {
    confidence: ".8",
    player_name: "Suggested Player",
    brand_name: "New Brand",
    season_start: null,
    is_autograph: false,
  },
} as unknown as Job;
it("supplement defaults to image-only confirmation even when suggestions differ", async () => {
  const post = vi.fn().mockResolvedValue({ id: "card" }),
    saved = vi.fn();
  render(
    <SupplementConfirmation
      api={
        {
          get: async () => ({ identity, can_accept_identity: true }),
          post,
        } as unknown as MobileApi
      }
      job={job}
      onSaved={saved}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认补充到原卡片" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/v1/capture/jobs/job/confirm", {}),
  );
  expect(saved).toHaveBeenCalledWith("card");
});
it("only explicitly selected primary fields change; unknown and annotation fields survive", async () => {
  const post = vi.fn().mockResolvedValue({ id: "card" });
  render(
    <SupplementConfirmation
      api={
        {
          get: async () => ({ identity, can_accept_identity: true }),
          post,
        } as unknown as MobileApi
      }
      job={job}
      onSaved={() => {}}
    />,
  );
  fireEvent.click(screen.getByText("查看识别差异（可选）"));
  const checkbox = await screen.findByRole("checkbox", {
    name: /Original Player.*Suggested Player/,
  });
  expect((checkbox as HTMLInputElement).checked).toBe(false);
  fireEvent.click(checkbox);
  fireEvent.click(
    screen.getByRole("button", { name: "接受所选差异并补充照片" }),
  );
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post.mock.calls[0][1]).toEqual({
    accepted_identity: { ...identity, player_name: "Suggested Player" },
  });
});

it("formally reviewed cards retain photo-only confirmation and explain where identity editing belongs", async () => {
  render(
    <SupplementConfirmation
      api={
        {
          get: async () => ({
            identity,
            can_accept_identity: false,
            identity_acceptance_reason: "请从身份审核修改",
          }),
          post: vi.fn(),
        } as unknown as MobileApi
      }
      job={job}
      onSaved={() => {}}
    />,
  );
  fireEvent.click(screen.getByText("查看识别差异（可选）"));
  await screen.findByText("请从身份审核修改");
  expect(
    screen
      .getByRole("button", { name: "接受所选差异并补充照片" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "确认补充到原卡片" })
      .hasAttribute("disabled"),
  ).toBe(false);
});
