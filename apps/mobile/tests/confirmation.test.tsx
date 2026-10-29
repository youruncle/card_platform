import React from "react";
import { afterEach, it, expect, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { ConfirmationForm } from "../src/ConfirmationForm";

afterEach(cleanup);
const pairedResult = (
  status: "unknown" | "conflict" | "consistent" = "unknown",
  back = "back-hash",
) => ({
  confidence: ".9",
  player_name: "Player",
  brand_name: "Topps",
  product_name: "Chrome",
  season_start: 2025,
  pair_validation: {
    status,
    reason_codes: ["identity_unverified"],
    evidence: ["背面卡号不可辨认"],
    image_content_hashes: { front: "front-hash", back },
    version: 1,
  },
});

it("keeps unknown pair evidence visible while saving without fabricating human review", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  render(
    <ConfirmationForm
      owner="owner"
      result={pairedResult()}
      paired
      onConfirm={save}
    />,
  );
  expect(screen.getByText("背面卡号不可辨认")).toBeTruthy();
  expect(screen.getByText(/正反面配对待确认/)).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  const submit = screen.getByRole("button", { name: "确认入库" });
  expect(submit.hasAttribute("disabled")).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).not.toHaveProperty("pair_review");
});

it("blocks known pair conflicts even when identity fields are filled", () => {
  const save = vi.fn();
  render(
    <ConfirmationForm
      owner="owner"
      result={pairedResult("conflict")}
      paired
      onConfirm={save}
    />,
  );
  expect(screen.getByText(/正反面存在明确冲突/)).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(
    screen.getByRole("button", { name: "确认入库" }).hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.submit(
    screen.getByRole("button", { name: "确认入库" }).closest("form")!,
  );
  expect(save).not.toHaveBeenCalled();
});

it("does not claim a human review when unknown images are replaced", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  const view = render(
    <ConfirmationForm
      owner="owner"
      result={pairedResult()}
      paired
      onConfirm={save}
    />,
  );
  view.rerender(
    <ConfirmationForm
      owner="owner"
      result={pairedResult("unknown", "new-back")}
      paired
      onConfirm={save}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).not.toHaveProperty("pair_review");
});

it("accepts an older paired result without inventing missing hashes", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  render(
    <ConfirmationForm
      owner="owner"
      result={{
        confidence: ".9",
        player_name: "Player",
        brand_name: "Topps",
        product_name: "Chrome",
        season_start: 2025,
      }}
      paired
      onConfirm={save}
    />,
  );
  expect(screen.getByText(/正反面配对待确认/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).not.toHaveProperty("pair_review");
});

it("keeps uncertainty visible and saves only the identity fields a person supplies", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  render(
    <ConfirmationForm
      owner="owner"
      result={{ confidence: "0.5", caveats: ["赛季不确定"] }}
      onConfirm={save}
    />,
  );
  expect(screen.getByText("赛季不确定")).toBeTruthy();
  expect(screen.getByText(/信息待确认/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("球员"), {
    target: { value: "Test Player" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).toMatchObject({
    card: {
      player_name: "Test Player",
      brand_name: null,
      product_name: null,
      season_start: null,
    },
  });
});

it("uses the exact shared identity transformer used by the web client", async () => {
  const mobile = await import("../src/confirmation");
  const shared = await import("../../../packages/card-core/src/confirmation");
  expect(mobile.confirmationPayload).toBe(shared.confirmationPayload);
});
