"use client";

import { use, useEffect, useState } from "react";
import { chatgptRequest } from "../../../../lib/chatgpt";

type Candidate = {
  url: string; title: string; image: string; back: string; price: string; currency: string; soldAt: string;
  seller: string; shipping: string; shippingKnown: boolean; bidCount: string;
  saleStatus: string; priceBasis: string; serialNumber: string; serialTotal: string;
  condition: string; finish: string; identityChecked: boolean; imagesChecked: boolean;
  conditionChecked: boolean; finishChecked: boolean; lot: boolean; duplicate: string;
};
type Run = { run_id: string; item_id: string; status: string; fair_value: number | null; currency: string;
  evidence_digest: string; packet: Record<string, unknown>; captures?: { ref: string; kind: string; url: string; source_url?: string }[] };
const initial = (): Candidate => ({ url: "", title: "", image: "", back: "", price: "", currency: "CNY", soldAt: "",
  seller: "", shipping: "", shippingKnown: false, bidCount: "", saleStatus: "sold", priceBasis: "auction_final_price",
  serialNumber: "", serialTotal: "", condition: "", finish: "", identityChecked: false, imagesChecked: false,
  conditionChecked: false, finishChecked: false, lot: false, duplicate: "unchecked" });
const statusLabels: Record<string,string> = { queued: "等待检索", running: "正在检索", review_required: "等待核图审核",
  completed: "已审核，证据支持观察区间", insufficient_evidence: "证据不足，价格未知", blocked: "研究存在缺口", failed: "研究失败" };
const textFields: { key: keyof Candidate; label: string; type?: string }[] = [
  {key:"url",label:"商品链接（eBay / 卡淘）"},{key:"title",label:"商品标题"},
  {key:"image",label:"商品实卡正面图片的公开链接"},{key:"back",label:"商品实卡背面图片链接（如有）"},
  {key:"price",label:"实际成交金额"},{key:"currency",label:"原币种（三字母代码）"},
  {key:"soldAt",label:"成交时间（带时区，例如 2026-10-20T20:00:00+08:00）"},
  {key:"seller",label:"卖家编号"},{key:"bidCount",label:"有效竞价次数"},
  {key:"shipping",label:"运费（单独列出）"},{key:"serialNumber",label:"实卡流水编号分子（如有）"},
  {key:"serialTotal",label:"限量总数（如有）"},{key:"condition",label:"候选实卡品相、瑕疵及与本卡的差异"},
  {key:"finish",label:"候选颜色、纹理和平行版本的核对依据"},
];

export default function ResearchPage({params}:{params:Promise<{runId:string}>}) {
  const {runId}=use(params);
  const [run,setRun]=useState<Run|null>(null);
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [checked,setChecked]=useState(false);
  const [targetCondition,setTargetCondition]=useState("");
  const [targetFinish,setTargetFinish]=useState("");
  const [fxCurrency,setFxCurrency]=useState("USD");
  const [fxRate,setFxRate]=useState("");
  const [fxDate,setFxDate]=useState("");
  const [fxUrl,setFxUrl]=useState("");
  const [sourceText,setSourceText]=useState<Record<string,string>>({});
  const load=()=>chatgptRequest<Run>(`/research/${encodeURIComponent(runId)}`).then(setRun);
  useEffect(()=>{load().catch(reason=>setError(String(reason)));},[runId]); // eslint-disable-line react-hooks/exhaustive-deps
  const change=(index:number,key:keyof Candidate,value:unknown)=>{setCandidates(previous=>previous.map((candidate,i)=>i===index?{...candidate,[key]:value}:candidate));setChecked(false);};
  return <main style={{maxWidth:900,margin:"32px auto",padding:24}}>
    <h1>核对成交研究</h1>
    {error&&<p role="alert">{error}</p>}
    {!run&&<p>请先在<a href="/">平台</a>登录后查看研究。</p>}
    {run&&<>
      <p>状态：{statusLabels[run.status]??run.status}。当前参考价格：{run.fair_value===null?"未知":`${run.fair_value} ${run.currency}`}。</p>
      <p>先核对本卡正背图及身份。身份有误时，请返回卡库校正，再重新研究。公开落槌不代表付款，未知议价金额、挂牌、整包及不同版本不能作为同款成交。</p>
      <div style={{display:"flex",gap:12,flexWrap:"wrap"}}>{["front","back"].map(side=><img key={side}
        src={`/v1/portfolio/items/${encodeURIComponent(run.item_id)}/images/${side}?variant=original`}
        alt={side==="front"?"本卡正面":"本卡背面"} style={{width:240,maxHeight:360,objectFit:"contain"}} />)}</div>
      <p>{Object.entries(run.packet.identity as Record<string,unknown>??{}).filter(([key])=>["player_name","brand_name","product_name","season_start","season_end","card_number","parallel_name","grade_company","grade_value"].includes(key)).map(([,value])=>value??"未知").join(" · ")}</p>
      {Array.isArray(run.packet.sales)&&run.packet.sales.length>0&&<section><h2>当前已保存候选</h2>
        {(run.packet.sales as Record<string,unknown>[]).map((sale,index)=><div key={index} style={{padding:12,borderBottom:"1px solid #888"}}>
          <p>候选 {index+1}：{String(sale.title??"")}</p><p>{String(sale.price??"金额未知")} {String(sale.currency??"")} · {String(sale.sold_at??"日期未知")} · 卖家 {String(sale.seller_id??"未知")}</p>
          <p>运费：{sale.shipping_status==="known"?String(sale.shipping):"未知"}；版本已核实：{sale.identity_confirmed?"是":"否"}；已核图：{sale.images_reviewed?"是":"否"}；整包：{sale.lot?"是":"否"}</p>
          <p>品相核对：{String((sale.condition as Record<string,unknown>)?.candidate??"未填写")}；纹理核对：{String((sale.finish as Record<string,unknown>)?.candidate??"未填写")}</p>
          <p>流水：{String(sale.serial_number??"未知")} / {String(sale.serial_total??"未知")}；去重：{sale.duplicate_check==="distinct_copy"?"已声明独立实卡":sale.duplicate_check==="owned_copy_history"?"本件历史 / 同实卡风险":"未核对"}</p>
          <a href={String(sale.url)} target="_blank" rel="noreferrer">商品来源</a>
        </div>)}</section>}
      <h2>本次原始捕获</h2>
      <p>原文仅作为文字展示，候选图片来自已保存的字节。请逐条核对标题与实图、版本、品相、日期、实际金额、币种、运费及是否同一实卡。</p>
      {run.captures?.map(capture=><section key={capture.ref} style={{borderBottom:"1px solid #888",padding:"12px 0"}}>
        {capture.kind==="image"?<img src={capture.url} alt="候选实卡捕获" style={{maxWidth:360,maxHeight:480,objectFit:"contain"}} />:<>
          <p>{capture.kind==="search"?"市场检索原文":"商品原文"} {capture.source_url&&<a href={capture.source_url} target="_blank" rel="noreferrer">打开来源</a>}</p>
          <button onClick={async()=>{try{const response=await fetch(capture.url,{credentials:"include"});if(!response.ok)throw new Error("读取捕获失败");const text=await response.text();setSourceText(previous=>({...previous,[capture.ref]:text}));}catch(reason){setError(String(reason));}}}>查看已保存原文</button>
          {sourceText[capture.ref]&&<pre style={{maxHeight:360,overflow:"auto",whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{sourceText[capture.ref]}</pre>}
        </>}
      </section>)}
      <h2>补充候选并保存取证</h2>
      <p>以下保存会重新访问商品链接和实卡图，保留原文；保存后还要检查新捕获并批准审核。取证在后台继续，可稍后刷新。服务器会排除资格不符或重复的成交，至少三条近期独立卖家同款公开成交才提供观察区间。</p>
      <label>本卡品相依据<input value={targetCondition} onChange={event=>setTargetCondition(event.target.value)} /></label>
      <label>本卡颜色与纹理依据<input value={targetFinish} onChange={event=>setTargetFinish(event.target.value)} /></label>
      <fieldset><legend>外币成交的折算依据（如需要）</legend>
        <label>原币种<input value={fxCurrency} onChange={event=>setFxCurrency(event.target.value)} /></label>
        <label>兑 {run.currency} 汇率<input value={fxRate} onChange={event=>setFxRate(event.target.value)} /></label>
        <label>来源日期（YYYY-MM-DD）<input value={fxDate} onChange={event=>setFxDate(event.target.value)} /></label>
        <label>公开汇率来源链接<input value={fxUrl} onChange={event=>setFxUrl(event.target.value)} /></label>
      </fieldset>
      {candidates.map((candidate,index)=><fieldset key={index} style={{margin:"20px 0",padding:16}}>
        <legend>候选 {index+1}</legend>
        {textFields.map(field=><label key={field.key} style={{display:"block",marginBottom:8}}>{field.label}
          <input style={{display:"block",width:"100%"}} value={String(candidate[field.key])} onChange={event=>change(index,field.key,event.target.value)} /></label>)}
        <label>商品状态<select value={candidate.saleStatus} onChange={event=>change(index,"saleStatus",event.target.value)}>
          <option value="sold">明确已成交</option><option value="ended_with_bid">拍卖结束且有有效竞价</option><option value="active">仍在挂牌</option><option value="removed">撤架 / 未证实成交</option></select></label>
        <label>金额依据<select value={candidate.priceBasis} onChange={event=>change(index,"priceBasis",event.target.value)}>
          <option value="auction_final_price">公开拍卖落槌金额</option><option value="fixed_price_sale">已确认一口价成交</option><option value="confirmed_best_offer">已取得实际议价金额凭证</option><option value="best_offer_unknown">Best Offer 实际金额未知</option></select></label>
        {([['shippingKnown','运费金额已知'],['identityChecked','已逐项核实球员、赛季、产品、完整卡号、平行、限量与评级一致'],['imagesChecked','已查看商品实卡图'],['conditionChecked','品相具有可比性'],['finishChecked','颜色和纹理具有可比性'],['lot','这是整包或多卡商品']] as [keyof Candidate,string][]).map(([key,label])=><label key={key} style={{display:"block"}}>
          <input type="checkbox" checked={Boolean(candidate[key])} onChange={event=>change(index,key,event.target.checked)} />{label}</label>)}
        <label>实卡去重<select value={candidate.duplicate} onChange={event=>change(index,"duplicate",event.target.value)}>
          <option value="unchecked">尚未核对</option><option value="distinct_copy">已核图、流水与卖家，属于独立实卡</option><option value="owned_copy_history">本件历史 / 疑似同一实卡再上</option></select></label>
        <button onClick={()=>{setCandidates(previous=>previous.filter((_,i)=>i!==index));setChecked(false);}}>移除此候选</button>
      </fieldset>)}
      <button disabled={candidates.length>=60||busy} onClick={()=>{setCandidates(previous=>[...previous,initial()]);setChecked(false);}}>增加候选</button>
      <button disabled={busy||candidates.length===0} onClick={async()=>{
        setBusy(true);setError("");setChecked(false);
        try {
          const sales=candidates.map(candidate=>({platform:new URL(candidate.url).hostname.includes("cardhobby")?"cardhobby":"ebay",url:candidate.url,title:candidate.title,
            identity:run.packet.identity,identity_confirmed:candidate.identityChecked,images_reviewed:candidate.imagesChecked,
            card_images:[{role:"card_front",url:candidate.image},...(candidate.back?[{role:"card_back",url:candidate.back}]:[])],
            sale_status:candidate.saleStatus,bid_count:candidate.bidCount?Number(candidate.bidCount):null,actual_price_known:candidate.priceBasis!=="best_offer_unknown"&&candidate.price!=="",
            price_basis:candidate.priceBasis,price:candidate.price||null,currency:candidate.currency,sold_at:candidate.soldAt,
            shipping:candidate.shippingKnown?candidate.shipping||null:null,shipping_status:candidate.shippingKnown?"known":"unknown",
            seller_id:candidate.seller,lot:candidate.lot,duplicate_check:candidate.duplicate,
            serial_number:candidate.serialNumber||null,serial_total:candidate.serialTotal||null,
            condition:{target:targetCondition,candidate:candidate.condition,comparable:candidate.conditionChecked},
            finish:{target:targetFinish,candidate:candidate.finish,comparable:candidate.finishChecked}}));
          await chatgptRequest(`/research/${encodeURIComponent(runId)}/draft`,{method:"POST",body:JSON.stringify({packet:{...run.packet,sales,fx:fxRate?[{base:fxCurrency,quote:run.currency,rate:fxRate,date:fxDate,url:fxUrl}]:run.packet.fx}})});
          await load();
        }catch(reason){setError(String(reason));}finally{setBusy(false);}
      }}>保存候选并启动后台取证</button>
      <h2>批准当前证据审核</h2>
      <label><input type="checkbox" checked={checked} onChange={event=>setChecked(event.target.checked)} />我已检查本卡及全部候选的当前原文、实卡图和核对字段，确认审核结论；不足的证据保持价格未知。</label>
      <button disabled={busy||!checked||["queued","running"].includes(run.status)} onClick={async()=>{
        setBusy(true);setError("");try{
          await chatgptRequest(`/research/${encodeURIComponent(runId)}/review`,{method:"POST",body:JSON.stringify({evidence_digest:run.evidence_digest,reviewed_all_candidates:true})});
          await load();setChecked(false);
        }catch(reason){setError(String(reason));}finally{setBusy(false);}
      }}>批准此版审核</button>
      <p>审核通过后返回 ChatGPT 准备发布；发布仍须在平台确认具体影响。审核不满足门槛时不会生成市场价格。</p>
      <button disabled={busy} onClick={()=>load().catch(reason=>setError(String(reason)))}>刷新任务</button>
    </>}
    <p><a href="/">返回卡库</a></p>
  </main>;
}
