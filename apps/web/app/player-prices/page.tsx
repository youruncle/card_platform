"use client";
import {useCallback,useEffect,useState} from "react";
import {PlayerPriceBoard} from "../../components/player-price-board";
import {getPlayerPrices,lastCompletedDate,refreshPlayerPrices,type PriceBoard} from "../../lib/player-prices";
import "./player-prices.css";

export default function PlayerPricesPage() {
  const [data,setData]=useState<PriceBoard>({report_date:lastCompletedDate(),timezone:"Asia/Shanghai",window_days:7,min_sales:3,currency:"CNY",players:[]});
  const [loading,setLoading]=useState(true),[refreshing,setRefreshing]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
  const load=useCallback(async()=>{
    setLoading(true);
    try {setData(await getPlayerPrices());setError("");} catch(e) {setError(e instanceof Error?e.message:"加载失败");}
    finally {setLoading(false);}
  },[]);
  useEffect(()=>{void load();},[load]);
  // Keep a queued update observable without reloading the whole page.
  useEffect(()=>{
    if(!data.players.some(p=>p.refresh.queued)||error) return;
    const timer=setInterval(()=>{void load();},30000);
    return ()=>clearInterval(timer);
  },[data,error,load]);
  async function refresh(){
    setRefreshing(true);setMessage("");
    try {const result=await refreshPlayerPrices();setMessage(`${result.message}（${result.queued} 位球员）`);await load();}
    catch(e) {setError(e instanceof Error?e.message:"更新失败");}
    finally {setRefreshing(false);}
  }
  return <PlayerPriceBoard data={data} error={error} loading={loading} refreshing={refreshing} onRefresh={()=>void refresh()} onReload={()=>void load()} message={message}/>;
}
