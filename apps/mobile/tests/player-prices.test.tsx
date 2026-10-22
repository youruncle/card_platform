import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { PlayerPricesPage } from "../src/PlayerPricesPage";
import type { MobileApi } from "../src/api";
import type { PriceBoard } from "../../../packages/card-core/src/player-prices";

afterEach(cleanup);
function board(): PriceBoard {
  const data:PriceBoard={report_date: "2026-10-11", timezone: "Asia/Shanghai", currency: "CNY", window_days: 7, min_sales: 3,
    worker_enabled: true, players: [
      {player_id: "up", player_name: "Curry", status: "ready", change_pct: "5", coverage: {}, refresh: {queued: false, error: null},
        cards: [{card_key: "one", identity: {product_name: "Prizm", parallel_name: "Silver"}, selection_count: 40,
          status: "ready", current_price: "105", previous_price: "100", current_count: 5, previous_count: 6,
          change_pct: "5", latest_sale_at: null, sources: [{source_id: "1", raw_title: "成交原始标题", sale_price: "105",
            occurred_at: "2026-10-11T01:00:00Z", source_url: "https://cardhobby.com.cn/Market/Item/1"}]}]},
      {player_id: "down", player_name: "Luka", status: "ready", change_pct: "-2", cards: [], coverage: {}, refresh: {queued: false, error: null}},
      {player_id: "none", player_name: "Unknown", status: "insufficient", change_pct: null, cards: [], coverage: {}, refresh: {queued: false, error: null}},
    ]};
  for(const player of data.players) {
    const reference=player.change_pct===null?null:{...data.players[0].cards[0],...player.cards[0],card_key:player.player_id,change_pct:player.change_pct};
    player.market={report_date:data.report_date,window_days:30,min_sales:3,sale_count:reference?11:0,card_count:reference?1:0,trend_count:reference?1:0,latest_sale_at:null,cards:reference?[reference]:[],cards_truncated:false,reference_card:reference};
  }
  return data;
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
  const reference=within(curry.querySelector('.price-card')!);
  expect(reference.getByText("¥105.00")).toBeTruthy();
  expect(reference.getByText("¥100.00")).toBeTruthy();
  fireEvent.click(reference.getByText("查看成交来源"));
  expect(reference.getByRole("link", {name: /成交原始标题/}).getAttribute("href")).toBe("https://cardhobby.com.cn/Market/Item/1");
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
    last_change_pct: "5", market:undefined, refresh: {queued: false, error: "采集失败"}}];
  render(<PlayerPricesPage api={{get: async () => data} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText("三卡7日指数待更新 · 上次统计 2026-10-09");
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
  expect(screen.getByText("代表卡待达标，三种均满足7日窗口条件后按月固定。")).toBeTruthy();
  expect(screen.queryByText(/代表卡固定于/)).toBeNull();
});

it("shows single-card monthly market without a player index and supports targeted refresh", async () => {
  const data = board();
  const card = {...data.players[0].cards[0], current_count: 4, previous_count: 4, change_pct: '20'};
  data.players = [{...data.players[0], cards: [], change_pct: null, status: 'insufficient',
    market: {report_date:'2026-10-11', window_days:30, min_sales:3, sale_count:8, card_count:1, trend_count:1,
      latest_sale_at:null, cards_truncated:false, cards:[card],reference_card:card}}];
  const post = vi.fn(async () => ({queued:1, message:'该球员已排队'}));
  render(<PlayerPricesPage api={{get:async () => data, post} as unknown as MobileApi} onBack={() => {}} />);
  await screen.findByText('Curry');
  fireEvent.click(screen.getByText('Curry'));
  expect(screen.getByText('近30日中位价')).toBeTruthy();
  expect(screen.getByText(/1 种可比卡款/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name:'更新 Curry 的成交'}));
  await screen.findByText('该球员已排队');
  expect(post).toHaveBeenCalledWith('/v1/player-prices/refresh?player_id=up');
});

it('exposes the server monthly reference before expanding, with identity and both sample windows', async () => {
  const data=board();
  const reference={...data.players[0].cards[0],identity:{product_name:'Prizm',card_number:'134',parallel_name:'Base'},current_price:'120',previous_price:'100',current_count:3,previous_count:4,change_pct:'20'};
  data.players=[{...data.players[0],change_pct:null,status:'insufficient',cards:[],market:{report_date:data.report_date,window_days:30,min_sales:3,sale_count:7,card_count:1,trend_count:1,latest_sale_at:null,cards:[],cards_truncated:false,reference_card:reference}}];
  render(<PlayerPricesPage api={{get:async()=>data} as unknown as MobileApi} onBack={()=>{}}/>);
  await screen.findByText('Curry');
  const row=screen.getByRole('group',{name:'Curry'});
  expect(within(row.querySelector('summary')!).getByText('+20.00%')).toBeTruthy();
  const referenceLine=row.querySelector('[data-price-reference]');
  expect(referenceLine).toBeTruthy();
  expect(referenceLine!.closest('details')).toBeNull();
  expect(referenceLine!.textContent).toContain('#134');
  expect(referenceLine!.textContent).toContain('100%');
  expect(referenceLine!.textContent).toContain('近30日 ¥120.00 · 3 笔');
  expect(referenceLine!.textContent).toContain('此前30日 ¥100.00 · 4 笔');
});

it('ranks monthly reference changes ahead of opposite index changes and leaves index-only missing',async()=>{
  const data=board();
  const base=data.players[0].cards[0];
  data.players=data.players.map((p,i)=>({...p,change_pct:i===0?'-99':'99',market:{report_date:data.report_date,window_days:30,min_sales:3,sale_count:7,card_count:1,trend_count:i===2?0:1,latest_sale_at:null,cards:[],cards_truncated:false,reference_card:i===2?null:{...base,change_pct:i===0?'20':'-10'}}}));
  render(<PlayerPricesPage api={{get:async()=>data} as unknown as MobileApi} onBack={()=>{}}/>);
  await screen.findByText('Curry');
  fireEvent.click(screen.getByRole('button',{name:'跌幅优先'}));
  expect(screen.getAllByRole('group',{name:/./}).map(e=>e.getAttribute('aria-label'))).toEqual(['Luka','Curry','Unknown']);
  const missing=screen.getByRole('group',{name:'Unknown'}).querySelector('summary')!;
  expect(missing.textContent).toContain('可比样本不足');
  expect(missing.textContent).not.toContain('99.00%');
});
