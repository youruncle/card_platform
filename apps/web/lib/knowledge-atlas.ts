import { requestKnowledgeAtlas } from './api';
import type { KnowledgeCard, KnowledgeCardDetail } from './knowledge-browser';
export type AtlasCard = KnowledgeCard & { atlas?: { identity_status: string; image_status?: string }; field_sources?: Record<string, FieldSource>; correction_conflict?: { reason: string } };
export type FieldSource = { basis?: string; sources?: {url:string|null;locator:string|null;authority:string|null}[]; evidence?: string[]; proposal_id?: string; reason?: string; reviewed_at?: string };
export type AtlasDetail = Omit<KnowledgeCardDetail,'card'> & { card: AtlasCard; version_id: string; base_fingerprint: string; field_sources: Record<string,FieldSource>; source_mappings?: {source_id:string; source_url?:string; role?:string; mapping_status:string; reasons?:string[]; source_asset_ids?:string[]; assertions?:Record<string,unknown>; sources?:{url:string|null;locator:string|null;authority:string|null}[]}[] };
export type AtlasPermissions = {role:string;can_propose:boolean;can_review:boolean};
export type AtlasSummary = {version:number|null;version_id:string|null;source_records:number;confirmed:number;pending:number;conflict:number;image_states:Record<string,number>;global_unique_atlases:null};
export type AtlasProposal = {id:string;card_id:string;base_version_id:string;base_fingerprint:string;changes:Record<string,unknown>;before:Record<string,unknown>;reason:string;evidence:string[];status:'pending'|'approved'|'rejected';submitted_by:string;submitted_at:string;published_once?:boolean;review_reason?:string;reviewed_at?:string};
export const atlasFields: Record<string,string> = {player:'球员',card_number:'卡号',section:'子集',parallel:'平行版本',serial_total:'总编',season_start:'起始年',season_end:'结束年',is_autograph:'签字属性'};
export const atlasGet = <T>(path:string,signal?:AbortSignal) => requestKnowledgeAtlas<T>(path,{signal});
export const atlasPost = <T>(path:string,payload:unknown,key:string) => requestKnowledgeAtlas<T>(path,{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify(payload)});
export const displayValue = (value:unknown):string => value === null || value === undefined || value === '' ? '未知' : value === true ? '是' : value === false ? '否' : String(value);
export function identityLabel(card:AtlasCard):string { const status=card.atlas?.identity_status;return status==='conflict'?'冲突':status==='confirmed_by_review'?'人工审核确认':status==='confirmed_within_official_source'?'来源内确认':'待确认'; }
export const proposalLabel = (status:string) => ({pending:'待审核',approved:'已批准 · 等待发布或已发布',rejected:'已驳回'}[status]??status);

/** Render a source-record assertion without leaking its transport metadata into prose. */
export function sourceClaimValue(claim: unknown): string {
  return displayValue(claim !== null && typeof claim === "object" && "value" in claim ? claim.value : claim);
}
