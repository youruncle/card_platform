import type {CaptureJob} from './contracts';
export type CaptureTaskState = 'processing'|'needs_review'|'failed'|'imported'|'cancelled';
export type CaptureTaskFilter = CaptureTaskState|'attention'|'all';
export type CaptureTaskEvent = {id:string;occurred_at:string;attempt_count:number;display_status:CaptureTaskState;message:string;source:string};
export type CaptureTask = CaptureJob & {display_status:CaptureTaskState;attention_reasons:string[];can_retry:boolean;image_content_hashes:Record<string,string>;history:CaptureTaskEvent[];replacement_job_id:string|null};
export type CaptureTaskPage = {items:CaptureTask[];counts:Record<CaptureTaskState|'total',number>;total:number;page:number;page_size:number;total_pages:number};
export const captureTaskLabels:Record<CaptureTaskState,string>={processing:'处理中',needs_review:'待确认 / 待补充',failed:'识别失败',imported:'已入库',cancelled:'已取消 / 已更正'};
