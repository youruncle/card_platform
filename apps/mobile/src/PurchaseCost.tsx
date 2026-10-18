import React, {useEffect,useRef,useState} from "react";
import {ApiError,type MobileApi} from "./api";
import {accountPath,costRequest,fetchPurchaseCost,type CostCandidate,type CostSearchResult,type CostPreview,type CostAudit} from "./purchaseCostApi";
type CardHobbyVerification={captcha_url:string;sms_sent:boolean;retry_after_seconds:number};
export function CardHobbyAccount({ api, verification = false, onConnected }: {api: MobileApi; verification?: boolean; onConnected: () => void}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState(verification);
  const [smsReady, setSmsReady] = useState(false);
  const [verificationVersion, setVerificationVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => setChallenge(verification), [verification]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (challenge && !smsReady) return;
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      if (challenge) await costRequest(api, `${accountPath}/verify`, {code}, controller.signal);
      else await costRequest(api, `${accountPath}/login`, {username,password}, controller.signal);
      if (!controller.signal.aborted) { setUsername(""); setPassword(""); setCode(""); onConnected(); }
    } catch (reason) {
      if (!controller.signal.aborted) {
        if (reason instanceof ApiError && reason.code === "VERIFICATION_REQUIRED") { setChallenge(true); setPassword(""); }
        if (reason instanceof ApiError && ["LOGIN_REQUIRED", "LOGIN_REJECTED"].includes(reason.code ?? "")) setChallenge(false);
        if (reason instanceof ApiError && reason.code === "VERIFICATION_EXPIRED") { setSmsReady(false); setVerificationVersion(value => value + 1); }
        setCode(""); setError(reason instanceof Error ? reason.message : "登录失败，请重试");
      }
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function forget() {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller; setBusy(true);
    try {
      await costRequest<CardHobbyVerification>(api, `${accountPath}/account`, undefined, controller.signal, "DELETE");
      if (!controller.signal.aborted) { setChallenge(false); setUsername(""); setPassword(""); setCode(""); setError("已移除当前用户保存的卡淘账号和登录会话。已有购入成本不受影响。"); }
    } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "移除失败"); }
    finally { request.current = null; if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="cost-connection">
    <h3>{challenge ? "卡淘短信验证" : "登录并绑定卡淘账号"}</h3>
    <p>仅绑定当前产品用户。登录成功后，账号、密码和会话将在后台加密保存；下次获取成本时自动检查登录状态，失效后自动续登。无需安装扩展。</p>
    <form onSubmit={event => void submit(event)}>
      {challenge ? <><CardHobbySmsVerification api={api} key={verificationVersion} onReady={setSmsReady} onConnected={onConnected} onLoginRequired={() => { setSmsReady(false); setChallenge(false); }} disabled={busy} />
        <label>短信验证码<input name="cardhobby-code" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{4,6}" title="请输入4至6位数字短信验证码" maxLength={6} required value={code} disabled={busy} onChange={e=>setCode(e.target.value)}/></label></> : <>
        <label>卡淘用户名 / 手机号<input name="cardhobby-username" autoComplete="username" required maxLength={120} value={username} disabled={busy} onChange={e=>setUsername(e.target.value)}/></label>
        <label>卡淘密码<input name="cardhobby-password" type="password" autoComplete="current-password" required maxLength={512} value={password} disabled={busy} onChange={e=>setPassword(e.target.value)}/></label>
      </>}
      <button className="primary-button" type="submit" disabled={busy || (challenge && !smsReady)}>{busy ? "正在验证…" : challenge ? "验证并继续获取成本" : "登录、保存并继续获取成本"}</button>
      <button className="text-button" type="button" disabled={busy} onClick={()=>void forget()}>移除已保存账号 / 重新绑定</button>
    </form>
    {error && <p role="alert">{error}</p>}
  </section>;
}

export function CardHobbySmsVerification({ api, onReady, onConnected, onLoginRequired, disabled = false }: {api: MobileApi; onReady: (ready: boolean) => void; onConnected?: () => void; onLoginRequired?: () => void; disabled?: boolean}) {
  const [details, setDetails] = useState<CardHobbyVerification | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const request = useRef<AbortController | null>(null);
  const callback = useRef(onReady);
  callback.current = onReady;

  function showError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : "无法读取验证状态，请重试");
    if (reason instanceof ApiError && reason.code === "VERIFICATION_EXPIRED") {
      setExpired(true); setDetails(null); setSeconds(0); setOpen(false); callback.current(false);
    }
    if (reason instanceof ApiError && ["LOGIN_REQUIRED", "LOGIN_REJECTED"].includes(reason.code ?? "")) onLoginRequired?.();
  }

  function apply(value: CardHobbyVerification) {
    const url = new URL(value.captcha_url);
    if (url.origin !== "https://cms.cardhobby.com.cn" || url.pathname !== "/checkcode/index") throw new Error("卡淘验证地址异常，请重新登录");
    setDetails(value); setSeconds(value.retry_after_seconds); setExpired(false);
    callback.current(value.sms_sent === true);
  }

  async function load(restart = false) {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      if (restart) {
        try {
          await costRequest<CardHobbyVerification>(api, `${accountPath}/restart-verification`, undefined, controller.signal, "POST");
          if (!controller.signal.aborted) onConnected?.();
          return;
        } catch (reason) {
          if (!(reason instanceof ApiError) || reason.code !== "VERIFICATION_REQUIRED") throw reason;
        }
      }
      const value = await costRequest<CardHobbyVerification>(api, `${accountPath}/verification`, undefined, controller.signal, "GET");
      if (!controller.signal.aborted) apply(value);
    } catch (reason) {
      if (!controller.signal.aborted) showError(reason);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    callback.current(false);
    void load();
    return () => { request.current?.abort(); request.current = null; };
  }, []);
  useEffect(() => {
    if (seconds <= 0) return;
    const timer = window.setTimeout(() => setSeconds(value => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [seconds]);

  useEffect(() => {
    if (!open || disabled) return;
    async function receive(event: MessageEvent) {
      if (event.origin !== "https://cms.cardhobby.com.cn" || !frame.current?.contentWindow || event.source !== frame.current.contentWindow || event.data !== "checkcode" || request.current) return;
      const controller = new AbortController(); request.current = controller;
      setOpen(false); setBusy(true); setError(""); callback.current(false);
      setDetails(value => value ? {...value, sms_sent: false} : value);
      try {
        const value = await costRequest<CardHobbyVerification>(api, `${accountPath}/send-sms`, undefined, controller.signal, "POST");
        if (!controller.signal.aborted) apply(value);
      } catch (reason) {
        if (!controller.signal.aborted) {
          showError(reason);
          // A failed/ambiguous POST also starts the server cooldown. Do not retry it.
          try {
            const value = await costRequest<CardHobbyVerification>(api, `${accountPath}/verification`, undefined, controller.signal, "GET");
            if (!controller.signal.aborted) apply(value);
          } catch (statusError) {
            if (!controller.signal.aborted) {
              if (statusError instanceof ApiError && ["VERIFICATION_EXPIRED", "LOGIN_REQUIRED"].includes(statusError.code ?? "")) showError(statusError);
              else setSeconds(60);
            }
          }
        }
      } finally {
        if (request.current === controller) request.current = null;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [open, disabled]);

  return <div className="cardhobby-sms-verification">
    <p role="status">{busy ? "正在确认短信状态…" : expired ? "验证已过期，可以使用已输入的账号重新发起验证。" : details?.sms_sent ? "卡淘已确认发送短信，请查看手机。" : "尚未确认发送短信，请先完成卡淘滑块验证。"}</p>
    <button type="button" className="text-button" disabled={busy || disabled || seconds > 0} onClick={() => {
      if (!details) { void load(expired); return; }
      setError(""); setOpen(true);
    }}>{seconds > 0 ? `${seconds}秒后可重新获取` : expired ? "重新发起验证" : !details ? "重新读取验证状态" : details.sms_sent ? "重新获取短信验证码" : "获取短信验证码"}</button>
    {open && details && <div>
      <p>请手动完成下方卡淘官方滑块，完成后将请求发送短信。</p>
      <iframe ref={frame} title="卡淘官方滑块验证" src={details.captcha_url} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer" style={{width:"100%", maxWidth:420, height:320, border:0}} />
      <button type="button" className="text-button" onClick={() => setOpen(false)}>取消滑块验证</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

const message=(e:unknown)=>e instanceof Error?e.message:'处理失败，请重试';
function safeLink(value:string){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:undefined;}catch{return undefined;}}
function Snapshot({candidate:c}:{candidate:CostCandidate}){return <section className="cost-order-snapshot"><strong>{c.title}</strong><p>订单 {c.order_id} · {c.ordered_at} · {c.seller} · {c.status}</p><p>{c.currency} {c.card_price} + 运费 {c.shipping} = {c.total_cost}</p><p>匹配方式 {c.match_method??'exact'} · 置信度 {c.confidence}</p><p>{c.reasons.join('；')}</p>{c.inference_warnings?.length&&<p className="notice">{c.inference_warnings.join('；')}</p>}{c.shipping_basis==='owner_default'&&<p className="notice">运费按本账户整单默认金额均摊，实际到付金额尚未核实。</p>}{c.visual_inference&&<p>图片推测（可能有误）：{c.visual_inference.reasons.join('；')}</p>}{c.image_urls.map(url=><img key={url} src={safeLink(url)} alt="订单商品快照" referrerPolicy="no-referrer" style={{maxWidth:'100%',maxHeight:240}}/>)}<a href={safeLink(c.source_url)} target="_blank" rel="noreferrer">查看来源订单</a></section>;}
export function PurchaseCostPanel({api,itemId,onChanged,initialResult}:{api:MobileApi;itemId:string;onChanged:()=>void;initialResult?:CostSearchResult}){
 const [result,setResult]=useState<CostSearchResult|null>(initialResult??null),[audit,setAudit]=useState<CostAudit|null>(null),[selected,setSelected]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[login,setLogin]=useState<string|null>(null),[price,setPrice]=useState(''),[currency,setCurrency]=useState('');
 const dirty=useRef(false);
 const active=useRef<AbortController|null>(null),mounted=useRef(true),pending=useRef(false);
 const path=`/v1/purchase-cost/items/${encodeURIComponent(itemId)}`;
 useEffect(()=>{mounted.current=true;active.current=null;setBusy(false);setLogin(null);pending.current=false;const c=new AbortController();setResult(initialResult??null);setAudit(null);setSelected('');setError('');setNotice('');dirty.current=false;setPrice('');setCurrency('');
 api.request<{item:{purchase_price?:string|number|null;purchase_currency?:string}}>(`/v1/portfolio/items/${encodeURIComponent(itemId)}/detail`,{signal:c.signal}).then(v=>{if(!c.signal.aborted&&!dirty.current&&v?.item){setPrice(v.item.purchase_price==null?'':String(v.item.purchase_price));setCurrency(v.item.purchase_currency??'');}}).catch(e=>{if(!c.signal.aborted)setError(message(e));});
 api.request<CostAudit|null>(`${path}/audit`,{signal:c.signal}).then(v=>{if(!c.signal.aborted)setAudit(v);}).catch(e=>{if(!c.signal.aborted)setError(message(e));});
 if(!initialResult)api.request<CostPreview>(`${path}/candidates`,{signal:c.signal}).then(v=>{if(!c.signal.aborted&&v?.candidates?.length)setResult({...v,status:'needs_confirmation'});}).catch(()=>{});
 return()=>{mounted.current=false;c.abort();active.current?.abort();};},[api,itemId,initialResult]);
 async function run(fn:(signal:AbortSignal)=>Promise<void>){if(active.current)return;const c=new AbortController();active.current=c;setBusy(true);setError('');try{await fn(c.signal);}catch(e){if(!c.signal.aborted&&mounted.current){setError(message(e));if(e instanceof ApiError&&['LOGIN_REQUIRED','LOGIN_REJECTED','VERIFICATION_REQUIRED'].includes(e.code??'')){pending.current=true;setLogin(e.code??'LOGIN_REQUIRED');}}}finally{if(active.current===c)active.current=null;if(mounted.current&&!c.signal.aborted)setBusy(false);}}
 function search(){void run(async signal=>{const value=await fetchPurchaseCost(api,itemId,signal);if(signal.aborted)return;setResult(value);setSelected('');setNotice(value.reason||value.status);if(value.status==='recorded'){onChanged();const v=await api.request<CostAudit|null>(`${path}/audit`,{signal});if(!signal.aborted)setAudit(v);}});}
 const candidates=result?.status==='needs_confirmation'?result.candidates.filter(c=>c.automatic):[];
 return <section className="panel"><h3>购入成本</h3><p>每次查询重新搜索订单；不能确定匹配时保留原成本。多笔记录须选择订单。</p><div className="actions"><button disabled={busy} onClick={search}>{busy?'正在处理…':result?'重新获取卡淘成本':'获取卡淘成本'}</button><button disabled={busy} onClick={()=>{pending.current=false;setLogin('LOGIN_REQUIRED');}}>管理卡淘账号</button></div>{login!==null&&<CardHobbyAccount api={api} verification={login==='VERIFICATION_REQUIRED'} onConnected={()=>{setLogin(null);if(pending.current){pending.current=false;search();}}}/>}{error&&<p className="error" role="alert">{error}</p>}{notice&&<p className="notice" role="status">{notice}</p>}
 {candidates.map(c=><label className="cost-candidate" key={c.unit_id}><input type="radio" name={`cost-${itemId}`} checked={selected===c.unit_id} disabled={busy} onChange={()=>setSelected(c.unit_id)}/><Snapshot candidate={c}/></label>)}{candidates.length>0&&<button disabled={busy||!selected} onClick={()=>void run(async signal=>{const candidate=candidates.find(c=>c.unit_id===selected);if(!candidate)return;await costRequest(api,`${path}/confirm`,{unit_id:candidate.unit_id,evidence_hash:candidate.evidence_hash,refresh:true},signal);if(signal.aborted)return;onChanged();setNotice('购入成本已录入');setResult({...result!,status:'recorded',recorded:candidate});const v=await api.request<CostAudit|null>(`${path}/audit`,{signal});if(!signal.aborted)setAudit(v);})}>确认所选商品并录入成本</button>}
 {(audit||result?.recorded)&&<details><summary>查看订单快照{audit?.manual_override?' · 已人工修改':''}</summary><Snapshot candidate={(audit?.evidence??result?.recorded)!}/>{audit?.source_changed&&<p role="alert">来源订单已变化，请核对退款或金额变化。原成本保留。</p>}{audit?.manual_override&&<p>当前人工价格 {audit.current_currency} {audit.current_price}，费用 {audit.current_shipping}；原始证据保留。</p>}</details>}
 <form onSubmit={e=>{e.preventDefault();if(!currency||price.trim()===''||!Number.isFinite(Number(price))||Number(price)<0){setError('请输入大于或等于零的价格');return;}void run(async signal=>{await costRequest(api,`/v1/portfolio/items/${encodeURIComponent(itemId)}`,{purchase_price:Number(price),purchase_currency:currency},signal,'PATCH');if(!signal.aborted){onChanged();setNotice('人工价格已保存，原始订单证据保留');const v=await api.request<CostAudit|null>(`${path}/audit`,{signal});if(!signal.aborted)setAudit(v);}});}}><label>购入价格<input type="number" min="0" step="0.01" required value={price} disabled={busy} onChange={e=>{dirty.current=true;setPrice(e.target.value);}}/></label><label>币种<select required value={currency} disabled={busy} onChange={e=>{dirty.current=true;setCurrency(e.target.value);}}><option value="">选择币种</option>{['CNY','USD','HKD','EUR','JPY','GBP','CAD','AUD'].map(c=><option key={c}>{c}</option>)}</select></label><button disabled={busy}>保存人工价格</button></form></section>;
}
