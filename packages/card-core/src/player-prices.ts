export type PriceSource = {source_id:string; raw_title:string; sale_price:string; occurred_at:string; source_url:string};
export type PriceCard = {
  card_key:string; identity:Record<string,unknown>; selection_count:number;
  status:"ready"|"insufficient"; current_price:string|null; previous_price:string|null;
  current_count:number; previous_count:number; change_pct:string|null; latest_sale_at:string|null;
  sources:PriceSource[];
};
export type PriceMarket = {
  report_date:string; window_days:number; min_sales:number;
  sale_count:number; card_count:number; trend_count:number;
  latest_sale_at:string|null; cards:PriceCard[]; cards_truncated:boolean;
  current_window?:{start:string;end:string}; previous_window?:{start:string;end:string};
};
export type PriceSummary = {tracked:number;with_sales:number;with_trends:number;with_index:number;queued:number;source_errors:number};
export type PriceSort = "activity"|"gainers"|"losers";
export type PricePlayer = {
  player_id:string; player_name:string; status:"ready"|"insufficient"|"pending"|"stale";
  change_pct:string|null; last_change_pct?:string|null; report_date?:string; cards:PriceCard[]; computed_at?:string|null;
  current_window?:{start:string;end:string}; previous_window?:{start:string;end:string};
  basket_selected_on?:string; basket_frozen?:boolean; basket_rebased?:boolean;
  market?:PriceMarket;
  coverage:{rejection_reasons?:Record<string,number>;sample_count?:number;identified_count?:number;rejected_count?:number;pages?:number;scope?:string;budget_exhausted?:boolean;local_limit_reached?:boolean};
  refresh:{queued:boolean;error:string|null;last_attempt_at?:string|null;last_success_at?:string|null};
};
export type PriceBoard = {
  report_date:string; timezone:string; window_days:number; min_sales:number; currency:string;
  players:PricePlayer[]; summary?:PriceSummary; worker_enabled?:boolean;
};
export function sortPlayers(players:PricePlayer[],direction:PriceSort) {
  return [...players].sort((a,b)=>{
    if(direction === "activity") {
      const activity = (b.market?.sale_count ?? b.cards.reduce((n,c)=>n+c.current_count,0)) - (a.market?.sale_count ?? a.cards.reduce((n,c)=>n+c.current_count,0));
      if(activity) return activity;
    }
    if(a.change_pct===null) return b.change_pct===null?a.player_name.localeCompare(b.player_name):1;
    if(b.change_pct===null) return -1;
    return (Number(a.change_pct)-Number(b.change_pct))*(direction==="gainers"?-1:1)
      || a.player_name.localeCompare(b.player_name);
  });
}
export function priceIdentity(identity:Record<string,unknown>) {
  return [identity.season_start,identity.product_name,
    identity.insert_name,identity.card_number?`#${identity.card_number}`:null,
    identity.parallel_name,(identity.serial_total||identity.print_run)?`/${identity.serial_total||identity.print_run}`:null,
    identity.grade_company?`${identity.grade_company} ${identity.grade_value??""}`:"裸卡"
  ].filter(Boolean).join(" · ");
}


export function priceSummary(data:PriceBoard):PriceSummary {
  return data.summary ?? {
    tracked:data.players.length,
    with_sales:data.players.filter(p=>(p.market?.sale_count ?? p.cards.reduce((n,c)=>n+c.selection_count,0))>0).length,
    with_trends:data.players.filter(p=>p.market ? p.market.trend_count>0 : p.cards.some(c=>c.change_pct!==null)).length,
    with_index:data.players.filter(p=>p.change_pct!==null).length,
    queued:data.players.filter(p=>p.refresh.queued).length,
    source_errors:data.players.filter(p=>Boolean(p.refresh.error)).length,
  };
}
export function marketDescription(player:PricePlayer):string {
  if(player.market) {
    const m=player.market;
    if(m.sale_count) return `近90日 ${m.sale_count} 笔确认成交 · ${m.card_count} 种卡款 · ${m.trend_count} 种可比卡款`;
    if(player.refresh.error) return "采集异常，暂未取得可确认成交";
    if(player.refresh.queued) return "等待补采公开成交";
    if(player.coverage.sample_count) return "已有成交候选，卡款身份尚未确认";
    return player.refresh.last_success_at ? "已检索，暂未取得可确认成交" : "尚未完成首次采集";
  }
  return `${player.cards.length}/3 种代表卡 · ${player.cards.reduce((n,c)=>n+c.current_count,0)} 笔近7日成交`;
}
const rejectionLabels:Record<string,string> = {
  conflicting_source:"成交信息冲突",invalid_observation:"成交证据无效",multi_card:"多卡组合",
  grade_unclear:"评级不明确",season_unclear:"年份不明确",identity_unresolved:"卡款身份未确认",
};
export function rejectionDescription(player:PricePlayer):string {
  return Object.entries(player.coverage.rejection_reasons ?? {}).map(([key,n])=>`${rejectionLabels[key] ?? "其他未纳入原因"} ${n}`).join(" · ");
}
