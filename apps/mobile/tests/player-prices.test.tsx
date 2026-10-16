import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { PlayerPricesPage } from "../src/PlayerPricesPage";
import type { MobileApi } from "../src/api";
import type { PriceBoard } from "../../../packages/card-core/src/player-prices";

afterEach(cleanup);
function board(): PriceBoard {
  return {report_date: "2026-10-11", timezone: "Asia/Shanghai", currency: "CNY", window_days: 7, min_sales: 3,
    worker_enabled: true, players: [
      {player_id: "up", player_name: "Curry", status: "ready", change_pct: "5", coverage: {}, refresh: {queued: false, error: null},
        cards: [{card_key: "one", identity: {product_name: "Prizm", parallel_name: "Silver"}, selection_count: 40,
          status: "ready", current_price: "105", previous_price: "100", current_count: 5, previous_count: 6,
          change_pct: "5", latest_sale_at: null, sources: [{source_id: "1", raw_title: "成交原始标题", sale_price: "105",
            occurred_at: "2026-10-11T01:00:00Z", source_url: "https://cardhobby.com.cn/Market/Item/1"}]}]},
      {player_id: "down", player_name: "Luka", status: "ready", change_pct: "-2", cards: [], coverage: {}, refresh: {queued: false, error: null}},
      {player_id: "none", player_name: "Unknown", status: "insufficient", change_pct: null, cards: [], coverage: {}, refresh: {queued: false, error: null}},
    ]};
}
it("sorts both directions, searches, and exposes two-window prices and original sources", async () => {
  render(<PlayerPricesPage api={{get: async () => board()} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText("Curry");
  expect(screen.getAllByRole("group", {name: /./}).map(e => e.getAttribute("aria-label"))).toEqual(["Curry", "Luka", "Unknown"]);
  fireEvent.click(screen.getByRole("button", {name: "跌幅优先"}));
  expect(screen.getAllByRole("group", {name: /./}).map(e => e.getAttribute("aria-label"))).toEqual(["Luka", "Curry", "Unknown"]);
  fireEvent.change(screen.getByLabelText("搜索球员"), {target: {value: "Curry"}});
  expect(screen.queryByText("Luka")).toBeNull();
  const curry = screen.getByRole("group", {name: "Curry"});
  fireEvent.click(within(curry).getByText("Curry"));
  expect(within(curry).getByText("¥105.00")).toBeTruthy();
  expect(within(curry).getByText("¥100.00")).toBeTruthy();
  fireEvent.click(within(curry).getByText("查看成交来源"));
  expect(within(curry).getByRole("link", {name: /成交原始标题/}).getAttribute("href")).toBe("https://cardhobby.com.cn/Market/Item/1");
});
it("keeps a real empty library empty and shows loading failure with retry", async () => {
  const get = vi.fn().mockRejectedValueOnce(new Error("连接失败")).mockResolvedValue({...board(), players: []});
  render(<PlayerPricesPage api={{get} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", {name: "重试"}));
  await screen.findByText("当前持仓还没有可统计的篮球球员。");
  expect(screen.queryByText("Curry")).toBeNull();
  expect(screen.queryByText(/演示/)).toBeNull();
});
it("shows stale date, insufficient result and source error without a false zero", async () => {
  const data = board();
  data.worker_enabled = false;
  data.players = [{...data.players[0], status: "stale", report_date: "2026-10-09", change_pct: null,
    last_change_pct: "5", refresh: {queued: false, error: "采集失败"}}];
  render(<PlayerPricesPage api={{get: async () => data} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText("待更新 · 上次统计 2026-10-09");
  expect(screen.getByText("采集失败")).toBeTruthy();
  expect(screen.queryByText("0.00%")).toBeNull();
  expect(screen.getByRole("button", {name: "更新公开成交"}).hasAttribute("disabled")).toBe(true);
});
it("requests an authenticated refresh and reloads queue state", async () => {
  const get = vi.fn(async () => board());
  const post = vi.fn(async () => ({queued: 1, message: "已排队"}));
  render(<PlayerPricesPage api={{get, post} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText("Curry");
  fireEvent.click(screen.getByRole("button", {name: "更新公开成交"}));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/v1/player-prices/refresh"));
  await screen.findByText("已排队");
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
});
it("marks capped sampling and distinguishes a provisional basket from a frozen basket", async () => {
  const data = board();
  data.players = [{...data.players[0], status: "insufficient", change_pct: null,
    basket_selected_on: "2026-10-11", basket_frozen: false,
    coverage: {sample_count: 720, identified_count: 3, rejected_count: 717, budget_exhausted: true, local_limit_reached: true}}];
  render(<PlayerPricesPage api={{get: async () => data} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText("采样已达上限，统计仅覆盖已取得的成交样本。");
  fireEvent.click(screen.getByText("Curry"));
  expect(screen.getByText("代表卡待补齐，凑齐三种后按月固定。")).toBeTruthy();
  expect(screen.queryByText(/代表卡固定于/)).toBeNull();
});
