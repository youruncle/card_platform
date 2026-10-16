import {LEGACY_CAPABILITIES,type ClientCapabilities} from '../../../packages/card-core/src/capture';
let pending:Promise<ClientCapabilities>|undefined;
/** Cache server metadata only. Transient errors remain retryable and never mean legacy support. */
export function captureCapabilities():Promise<ClientCapabilities>{
 pending??=fetch(`${process.env.NEXT_PUBLIC_API_URL??''}/v1/client-capabilities`,{credentials:'include'}).then(async response=>{
  if(response.status===404)return LEGACY_CAPABILITIES;
  if(!response.ok)throw Error(`无法读取服务器图片能力 (${response.status})`);
  return response.json() as Promise<ClientCapabilities>;
 }).catch(error=>{pending=undefined;throw error;});
 return pending;
}
