import {type MobileApi} from "./api";
export type CostCandidate = {
  unit_id: string; evidence_hash: string; order_id: string; ordered_at: string;
  seller: string; status: string; item_id: string; unit_index: number; title: string;
  image_urls: string[]; source_url: string; card_price: string; shipping: string;
  shipping_method?: "prepaid" | "sf_collect"; shipping_basis?: "order" | "owner_default";
  total_cost: string; currency: string; confidence: string; reasons: string[];
  missing: string[]; automatic: boolean;
  item_url?: string; match_method?: "exact" | "inferred" | "image_title_correction" | "bundle_equal_split"; inference_warnings?: string[];
  visual_inference?: {verdict: string; confidence: string; reasons: string[]};
};
export type CostPreview = { candidates: CostCandidate[]; rejected: {item_id: string; reasons: string[]}[]; reason: string };
export type CostAudit = { card_price: string; shipping: string; confirmation: string; source_changed: boolean; manual_override: boolean; evidence: CostCandidate; current_price: string; current_shipping: string; current_currency: string };

export type CostSearchPlan={item_id:string;label:string;keywords:string[];fallback_keywords?:string[];status:string;reason:string};
export type CostSearchResult=CostPreview & {status:string;recorded?:CostCandidate};
export const accountPath="/v1/purchase-cost/cardhobby";
export function costRequest<T>(api:MobileApi,path:string,body?:unknown,signal?:AbortSignal,method="POST"):Promise<T>{return api.request<T>(path,{method,headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),signal},false,180000);}
export async function fetchPurchaseCost(api:MobileApi,id:string,signal?:AbortSignal,options:{refresh?:boolean}={}):Promise<CostSearchResult>{
 const path=`/v1/purchase-cost/items/${encodeURIComponent(id)}`;
 const plan=await api.request<CostSearchPlan>(`${path}/search-plan`,{signal});
 if(plan.status==="skipped")return {status:"skipped",reason:plan.reason,candidates:[],rejected:[]};
 type Capture={orders:unknown[];complete:boolean};
 const search=async(keywords:string[])=>{const value=await costRequest<Capture>(api,`${accountPath}/search`,{keywords},signal);if(!value||!Array.isArray(value.orders)||typeof value.complete!=="boolean")throw new Error("卡淘未返回有效搜索结果，请重试");return value;};
 const resolve=(capture:Capture)=>costRequest<CostSearchResult>(api,`${path}/search-result`,{...capture,refresh:options.refresh??true},signal);
 const capture=await search(plan.keywords);const first=await resolve(capture);
 if(signal?.aborted||!capture.complete||!plan.fallback_keywords?.length||!["not_found","unmatched"].includes(first.status))return first;
 const fallback=await search(plan.fallback_keywords);if(signal?.aborted)return first;
 const orders=new Map<string,unknown>();for(const order of [...capture.orders,...fallback.orders]){const key=(order as {external_order_id?:string}|null)?.external_order_id;if(!key)throw new Error("卡淘订单缺少编号，未继续写入成本");orders.set(key,order);}
 return resolve({orders:[...orders.values()].slice(0,100),complete:fallback.complete&&orders.size<=100});
}
