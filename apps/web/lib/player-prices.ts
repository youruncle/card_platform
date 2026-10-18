import type { PriceBoard, PriceCard } from "../../../packages/card-core/src/player-prices";
export type { PriceSource, PriceCard, PricePlayer, PriceBoard } from "../../../packages/card-core/src/player-prices";
export { sortPlayers, priceIdentity, priceSummary, marketDescription, rejectionDescription } from "../../../packages/card-core/src/player-prices";
export function demoBoard(reportDate:string):PriceBoard {
  const start=new Date(`${reportDate}T00:00:00+08:00`);
  const cards=(change:number|null):PriceCard[]=>["Prizm","Donruss Optic","Hoops"].map((product,i)=>({
    card_key:`demo-${i}`,identity:{season_start:2024,product_name:product,card_number:23+i,
      parallel_name:i===0?"Silver":"Base",grade_company:i===1?"PSA":null,grade_value:i===1?10:null},
    selection_count:30-i*5,status:change===null?"insufficient":"ready",current_price:change===null?null:String((100+i*50)*(1+change/100)),
    previous_price:String(100+i*50),current_count:change===null?0:7+i,previous_count:8+i,
    change_pct:change===null?null:change.toFixed(4),latest_sale_at:start.toISOString(),sources:[]
  }));
  return {report_date:reportDate,timezone:"Asia/Shanghai",window_days:7,min_sales:3,currency:"CNY",players:[
    ["Stephen Curry",8.5],["Luka Dončić",-6.2],["LeBron James",0],["Victor Wembanyama",null]
  ].map(([name,change],i)=>({player_id:`demo-${i}`,player_name:String(name),status:change===null?"insufficient":"ready",
    change_pct:change===null?null:Number(change).toFixed(4),cards:cards(change===null?null:Number(change)),
    basket_selected_on:reportDate,basket_frozen:true,coverage:{sample_count:180,identified_count:95,rejected_count:85,pages:4},
    refresh:{queued:false,error:null}}))};
}
const API_URL=process.env.NEXT_PUBLIC_API_URL??"";
async function request(path:string,method="GET") {
  const response=await fetch(`${API_URL}${path}`,{method,credentials:"include",cache:"no-store"});
  if(!response.ok) {
    if(response.status===401) throw new Error("请先登录资产台，再查看你的球员卡价统计。");
    const data=await response.json().catch(()=>({}));
    throw new Error(typeof data.detail==="string"?data.detail:"球员卡价统计暂时无法加载，请稍后重试。");
  }
  return response.json();
}
export async function getPlayerPrices(date?:string):Promise<PriceBoard> {
  return request(`/v1/player-prices${date?`?date=${encodeURIComponent(date)}`:""}`);
}
export async function refreshPlayerPrices(playerId?:string):Promise<{queued:number;message:string}> {
  return request(`/v1/player-prices/refresh${playerId?`?player_id=${encodeURIComponent(playerId)}`:""}`,"POST");
}
export function lastCompletedDate() {
  // Calendar subtraction in Shanghai, independent from browser time zone.
  const today=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  return new Date(new Date(`${today}T12:00:00+08:00`).getTime()-86400000).toISOString().slice(0,10);
}
