import React from "react";
import { it, expect, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { ConfirmationForm } from "../src/ConfirmationForm";

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
