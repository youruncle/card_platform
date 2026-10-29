import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ConfirmationForm } from "../src/ConfirmationForm";
import { LibraryPage, DetailPage } from "../src/LibraryPage";
import type { MobileApi } from "../src/api";

beforeEach(() => {
  window.scrollTo = vi.fn();
});
afterEach(cleanup);
const incomplete = {
  id: "incomplete",
  player_name: null,
  brand_name: null,
  product_name: null,
  season_start: null,
  remaining_cost: "0",
  identity_review_required: true,
  identity_missing_fields: [
    "player_name",
    "brand_name",
    "product_name",
    "season_start",
    "is_autograph",
    "pair_validation",
  ],
};

it("saves blank identity fields as null and marks unknown information without requiring a pair affirmation", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  render(
    <ConfirmationForm
      owner="owner"
      result={{ confidence: 0 }}
      paired
      onConfirm={save}
    />,
  );
  expect(screen.getByText(/信息待确认/)).toBeTruthy();
  expect((screen.getByLabelText("球员") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("赛季年份") as HTMLInputElement).value).toBe(
    "",
  );
  expect(screen.queryByRole("checkbox")).toBeNull();
  const submit = screen.getByRole("button", { name: "确认入库" });
  expect(submit.hasAttribute("disabled")).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  const payload = save.mock.calls[0][0] as {
    card: Record<string, unknown>;
    pair_review?: unknown;
  };
  expect(payload.card).toMatchObject({
    player_name: null,
    brand_name: null,
    product_name: null,
    season_start: null,
    is_rookie: null,
    is_autograph: null,
  });
  expect(payload).not.toHaveProperty("pair_review");
});

it("renders unconfirmed cards with a badge and blanks, and clears the badge after saved information is refreshed", async () => {
  let card: Record<string, unknown> = incomplete;
  const api = {
    get: vi.fn(async () => ({
      items: [card],
      total: 1,
      page: 1,
      total_pages: 1,
    })),
  } as unknown as MobileApi;
  render(<LibraryPage api={api} onOpen={() => {}} />);
  expect(await screen.findByText("信息待确认的卡片")).toBeTruthy();
  expect(screen.getByText("信息待确认", { exact: true })).toBeTruthy();
  expect(
    screen.getByText(/待补充：球员、品牌、产品、赛季年份、签字卡、正反面配对/),
  ).toBeTruthy();
  expect(screen.getByText("— · —", { exact: true })).toBeTruthy();
  card = {
    ...card,
    player_name: "Player",
    brand_name: "Topps",
    product_name: "Chrome",
    season_start: 2025,
    identity_review_required: false,
    identity_missing_fields: [],
  };
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await screen.findByText("Player", { exact: true });
  expect(screen.queryByText("信息待确认", { exact: true })).toBeNull();
});

it("shows the saved identity marker and missing-field names in card details without inventing a name or year", async () => {
  const api = {
    get: vi.fn(async (path: string) =>
      path.endsWith("/detail")
        ? { item: incomplete }
        : { status: null, error_message: null, comparables: [] },
    ),
  } as unknown as MobileApi;
  render(<DetailPage api={api} id="incomplete" onBack={() => {}} />);
  expect(
    await screen.findByRole("heading", { name: "信息待确认的卡片" }),
  ).toBeTruthy();
  expect(screen.getByText("— · — · —", { exact: true })).toBeTruthy();
  expect(screen.getByText("信息待确认", { exact: true })).toBeTruthy();
  expect(screen.getByText(/正反面配对/)).toBeTruthy();
});

it("leaves unknown or conflicted model guesses blank while preserving verified facts", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  const result = {
    confidence: 0.7,
    player_name: "Unverified Player",
    brand_name: "Topps",
    product_name: "Guessed Chrome",
    season_start: 2025,
    is_rookie: true,
    is_autograph: false,
    field_assessments: {
      player_name: { status: "unknown" as const },
      brand_name: { status: "verified" as const },
      product_name: { status: "conflicted" as const },
      season_start: { status: "unknown" as const },
      is_rookie: { status: "conflicted" as const },
      is_autograph: { status: "verified" as const },
    },
  };
  render(<ConfirmationForm owner="owner" result={result} onConfirm={save} />);
  expect((screen.getByLabelText("球员") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("品牌") as HTMLInputElement).value).toBe(
    "Topps",
  );
  expect((screen.getByLabelText("产品") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("赛季年份") as HTMLInputElement).value).toBe(
    "",
  );
  expect((screen.getByLabelText("新秀卡") as HTMLSelectElement).value).toBe("");
  expect((screen.getByLabelText("签字卡") as HTMLSelectElement).value).toBe(
    "false",
  );
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).toMatchObject({
    card: {
      player_name: null,
      brand_name: "Topps",
      product_name: null,
      season_start: null,
      is_rookie: null,
      is_autograph: false,
    },
  });
});

it("allows a person to supply a value after an unknown model assessment", async () => {
  const save = vi.fn(async (_payload: unknown) => {});
  const result = {
    confidence: 0.4,
    season_start: 2025,
    field_assessments: { season_start: { status: "unknown" as const } },
  };
  render(<ConfirmationForm owner="owner" result={result} onConfirm={save} />);
  fireEvent.change(screen.getByLabelText("赛季年份"), {
    target: { value: "2024" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认入库" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0][0]).toMatchObject({ card: { season_start: 2024 } });
});
