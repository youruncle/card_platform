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
  reference_card?:PriceCard|null;
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
export function marketReference(player:PricePlayer,reportDate?:string):PriceCard|null {
  const market=player.market,card=market?.reference_card;
  if(!market || market.window_days!==30 || (reportDate && market.report_date!==reportDate)
    || !card || card.status!=='ready' || card.current_count<Math.max(3,market.min_sales)
    || card.previous_count<Math.max(3,market.min_sales) || card.change_pct===null
    || !Number.isFinite(Number(card.change_pct))) return null;
  return card;
}
export function marketCards(player:PricePlayer,reportDate?:string):PriceCard[] {
  if(!player.market) return player.cards;
  const reference=marketReference(player,reportDate);
  return reference ? [reference,...player.market.cards.filter(card=>card.card_key!==reference.card_key)].slice(0,20) : player.market.cards;
}
export function marketWindowDescription(player:PricePlayer):string {
  const market=player.market;
  if(!market?.current_window || !market.previous_window) return '';
  const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
  const dates=[market.current_window.start,market.current_window.end,market.previous_window.start,market.previous_window.end].map(value=>new Date(value).getTime());
  if(dates.some(value=>!Number.isFinite(value))) return '';
  return `近30日 ${format.format(dates[0])}—${format.format(dates[1]-1)} · 此前30日 ${format.format(dates[2])}—${format.format(dates[3]-1)}`;
}
export function sortPlayers(players:PricePlayer[],direction:PriceSort,reportDate?:string) {
  return [...players].sort((a,b)=>{
    if(direction === "activity") {
      const activityOf=(player:PricePlayer)=>player.market && (!reportDate || player.market.report_date===reportDate) ? player.market.sale_count : null;
      const aa=activityOf(a),ba=activityOf(b);
      if(aa===null) return ba===null?a.player_name.localeCompare(b.player_name):1;
      if(ba===null) return -1;
      const activity = ba-aa;
      if(activity) return activity;
    }
    const av=marketReference(a,reportDate)?.change_pct ?? null,bv=marketReference(b,reportDate)?.change_pct ?? null;
    if(av===null) return bv===null?a.player_name.localeCompare(b.player_name):1;
    if(bv===null) return -1;
    return (Number(av)-Number(bv))*(direction==="losers"?1:-1)
      || a.player_name.localeCompare(b.player_name);
  });
}
export function priceIdentity(identity:Record<string,unknown>) {
  const season=identity.season_start && identity.season_end && identity.season_start!==identity.season_end
    ? `${identity.season_start}-${String(identity.season_end).slice(-2)}` : identity.season_start;
  return [season,identity.brand_name,identity.product_name,
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
export function marketDescription(player:PricePlayer,reportDate?:string):string {
  if(player.market) {
    const m=player.market;
    const prior=reportDate && m.report_date!==reportDate ? `旧行情 ${m.report_date} · ` : '';
    if(m.sale_count) return `${prior}近90日 ${m.sale_count} 笔确认成交 · ${m.card_count} 种卡款 · ${m.trend_count} 种可比卡款`;
    if(prior) return `${prior}等待更新公开成交`;
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
