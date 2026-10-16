export type PriceSource = {source_id:string; raw_title:string; sale_price:string; occurred_at:string; source_url:string};
export type PriceCard = {
  card_key:string; identity:Record<string,unknown>; selection_count:number;
  status:"ready"|"insufficient"; current_price:string|null; previous_price:string|null;
  current_count:number; previous_count:number; change_pct:string|null; latest_sale_at:string|null;
  sources:PriceSource[];
};
export type PricePlayer = {
  player_id:string; player_name:string; status:"ready"|"insufficient"|"pending"|"stale";
  change_pct:string|null; last_change_pct?:string|null; report_date?:string; cards:PriceCard[]; computed_at?:string|null;
  current_window?:{start:string;end:string}; previous_window?:{start:string;end:string};
  basket_selected_on?:string; basket_frozen?:boolean; basket_rebased?:boolean;
  coverage:{sample_count?:number;identified_count?:number;rejected_count?:number;pages?:number;scope?:string;budget_exhausted?:boolean;local_limit_reached?:boolean};
  refresh:{queued:boolean;error:string|null;last_attempt_at?:string|null;last_success_at?:string|null};
};
export type PriceBoard = {
  report_date:string; timezone:string; window_days:number; min_sales:number; currency:string;
  players:PricePlayer[]; worker_enabled?:boolean;
};
export function sortPlayers(players:PricePlayer[],direction:"gainers"|"losers") {
  return [...players].sort((a,b)=>{
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
