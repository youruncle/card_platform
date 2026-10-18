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
const pairedResult = (status: "unknown" | "conflict" | "consistent" = "unknown", back = "back-hash") => ({
  confidence: ".9", player_name: "Player", brand_name: "Topps", product_name: "Chrome", season_start: 2025,
  pair_validation: {status, reason_codes: ["identity_unverified"], evidence: ["背面卡号不可辨认"], image_content_hashes: {front: "front-hash", back}, version: 1},
});

it("requires a separate review of the current two images before confirming an unknown pair", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(<ConfirmationForm owner="owner" result={pairedResult()} paired onConfirm={save} />);
  expect(screen.getByText("背面卡号不可辨认")).toBeTruthy();
  const submit = screen.getByRole("button", {name: "确认入库"});
  expect(submit.hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByLabelText("我已查看当前正反面，确认属于同一张卡"));
  fireEvent.click(submit);
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0].pair_review).toEqual({confirmed: true, image_content_hashes: {front: "front-hash", back: "back-hash"}});
});

it("blocks known pair conflicts even when identity fields are filled", () => {
  const save = vi.fn();
  render(<ConfirmationForm owner="owner" result={pairedResult("conflict")} paired onConfirm={save} />);
  expect(screen.getByText(/正反面存在明确冲突/)).toBeTruthy();
  expect(screen.queryByLabelText("我已查看当前正反面，确认属于同一张卡")).toBeNull();
  expect(screen.getByRole("button", {name: "确认入库"}).hasAttribute("disabled")).toBe(true);
  fireEvent.submit(screen.getByRole("button", {name: "确认入库"}).closest("form")!);
  expect(save).not.toHaveBeenCalled();
});

it("invalidates the explicit review when the current image hashes change", () => {
  const save = vi.fn();
  const view = render(<ConfirmationForm owner="owner" result={pairedResult()} paired onConfirm={save} />);
  fireEvent.click(screen.getByLabelText("我已查看当前正反面，确认属于同一张卡"));
  view.rerender(<ConfirmationForm owner="owner" result={pairedResult("unknown", "new-back")} paired onConfirm={save} />);
  expect((screen.getByLabelText("我已查看当前正反面，确认属于同一张卡") as HTMLInputElement).checked).toBe(false);
  expect(screen.getByRole("button", {name: "确认入库"}).hasAttribute("disabled")).toBe(true);
});

it("treats a paired legacy result as unknown without inventing review hashes", () => {
  render(<ConfirmationForm owner="owner" result={{confidence: ".9", player_name: "Player", brand_name: "Topps", product_name: "Chrome", season_start: 2025}} paired onConfirm={vi.fn()} />);
  expect(screen.getByText(/请重新识别当前两面照片/)).toBeTruthy();
  expect(screen.getByRole("button", {name: "确认入库"}).hasAttribute("disabled")).toBe(true);
});

it("requires human fields and displays uncertainty before saving", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(
    <ConfirmationForm
      owner="owner"
      result={{ confidence: "0.5", caveats: ["赛季不确定"] }}
      onConfirm={save}
    />,
  );
  expect(screen.getByText("赛季不确定")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  expect(save).not.toHaveBeenCalled();
  for (const [label, value] of [
    ["球员", "Test Player"],
    ["品牌", "Test"],
    ["产品", "Test"],
    ["赛季年份", "2025"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0].card.season_start).toBe(2025);
  cleanup();
});

it("uses the exact shared identity transformer used by the web client", async () => {
  const mobile = await import("../src/confirmation");
  const shared = await import("../../../packages/card-core/src/confirmation");
  expect(mobile.confirmationPayload).toBe(shared.confirmationPayload);
});
