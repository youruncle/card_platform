import { requestKnowledgeAtlas } from './api';
import type { KnowledgeCard, KnowledgeCardDetail } from './knowledge-browser';
export type AtlasCard = KnowledgeCard & { atlas?: { identity_status: string; image_status?: string; metadata_scope?: string; serial_status?:string; autograph_status?:string; physical_autograph_status?:null }; source_atlas?:{scope:string;serial_status?:string;autograph_status?:string}; original_identity?:Record<string,unknown>; correction_effects?:{pricing_groups:string;image_bindings:string}; field_sources?: Record<string, FieldSource>; correction_conflict?: { reason: string } };
export type FieldSource = { basis?: string; sources?: {url:string|null;locator:string|null;authority:string|null}[]; evidence?: string[]; proposal_id?: string; reason?: string; reviewed_at?: string };
export type AtlasDetail = Omit<KnowledgeCardDetail,'card'> & { card: AtlasCard; version_id: string; base_fingerprint: string; field_sources: Record<string,FieldSource>; source_mappings?: {source_id:string; source_url?:string; role?:string; mapping_status:string; reasons?:string[]; source_asset_ids?:string[]; assertions?:Record<string,unknown>; sources?:{url:string|null;locator:string|null;authority:string|null}[]}[] };
export type AtlasPermissions = {role:string;can_propose:boolean;can_review:boolean};
export type AtlasSummary = {version:number|null;version_id:string|null;source_records:number;confirmed:number;pending:number;conflict:number;image_states:Record<string,number>;global_unique_atlases:null};
export type AtlasVersionRef = {id:string;number:number|null};
export type AtlasTimelineEvent = {id:string;kind:string;at:string;actor_id:string|null;version?:AtlasVersionRef;from_version?:AtlasVersionRef;reason:string|null;time_basis?:string};
export type AtlasLifecycle = {state:string;current_version:AtlasVersionRef|null;current_version_contains:boolean;application_versions:AtlasVersionRef[];timeline:AtlasTimelineEvent[];timeline_order_uncertain?:boolean;scope:string};
export type AtlasProposal = {id:string;card_id:string;base_version_id:string;base_fingerprint:string;changes:Record<string,unknown>;before:Record<string,unknown>;reason:string;evidence:string[];status:'pending'|'approved'|'rejected';submitted_by:string;submitted_at:string;published_once?:boolean;lifecycle?:AtlasLifecycle;review_reason?:string;reviewed_at?:string};
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

export function proposalStateLabel(item: AtlasProposal): string {
  const labels:Record<string,string>={pending:'待审核',approved_draft:'已批准 · 待显式发布',stale_draft:'已批准 · 依据版本过期',current:'已发布 · 当前版本包含',rolled_back:'已发布 · 已回滚移除',not_current:'已发布 · 当前版本未包含',rejected:'已驳回'};
  return item.lifecycle ? labels[item.lifecycle.state]??proposalLabel(item.status) : item.status==='approved'?(item.published_once?'已批准 · 已进入发布历史':'已批准 · 待显式发布'):proposalLabel(item.status);
}
export const timelineKindLabel = (kind:string):string => ({submitted:'提交提案',approved:'审核批准',rejected:'审核驳回',published:'首次发布',carried_forward:'后续发布携带',rollback_removed:'回滚移除',rollback_restored:'回滚恢复',rollback_retained:'回滚保留'} as Record<string,string>)[kind]??kind;
export const autographStateLabel = (state?:string):string => ({attribute_true:'是',attribute_false:'否',manufacturer_named_autograph_family:'厂家原命名含签字系列',unknown:'未知'} as Record<string,string>)[state??'unknown']??'未知';
export const serialStateLabel = (state?:string):string => ({known:'已知总编',not_applicable:'来源确认无编号',unknown:'未知'} as Record<string,string>)[state??'unknown']??'未知';

export function pairBindingCaption(detail:AtlasDetail):string {
  const mappings=detail.source_mappings??[];
  const conflict=mappings.some(mapping=>mapping.reasons?.some(reason=>reason.endsWith('_conflict')||reason==='conflicting_same_record_faces'||reason==='conflicting_or_invalid_specimen_serial'));
  return conflict?'图片来源绑定存在冲突，待核对':mappings.some(mapping=>mapping.mapping_status==='needs_review')?'图片来源绑定待核对':detail.card.atlas?.image_status==='paired'?'已有来源审核配对':'图片配对未确认';
}
