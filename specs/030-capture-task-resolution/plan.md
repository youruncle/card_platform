# Capture Task Resolution Implementation Plan

> Agentic execution: use executing-plans for backend, dispatching-parallel-agents for independently owned web/mobile clients; test-driven-development and verification-before-completion throughout.

**Goal:** Every capture outcome is visible and recoverable without false identity certainty.
**Architecture:** Add a paginated task API on the existing capture router, derive business state, preserve append-only PostgreSQL task events. Clients consume one shared contract. Existing confirmation/image ownership services remain authoritative. Nullable recognition booleans and core-identity readiness protect imports.
**Tech Stack:** Existing Python/FastAPI/psycopg, React/TypeScript, Capacitor/iOS; no new runtime dependencies.
**Spec:** spec.md

## Global Constraints

Preserve dirty work; use only task-related commits. Cloud-only data; worker=true, deepseek-flash/thinking disabled, isolated Bailian search. No blanket permission prompts: implementation and release explicitly authorized. master may need creation because repository currently uses main; do not change GitHub default branch.

## Interfaces

GET /v1/capture/tasks?state=attention|all|processing|needs_review|failed|imported|cancelled&page=1&page_size=20 -> CaptureTaskPage.
GET /v1/capture/tasks/{id} -> CaptureTask, history included.
POST /v1/capture/tasks/{id}/retry -> CaptureTask.
POST /v1/capture/tasks/{id}/confirm -> CreatedResource, same card/pair_review payload as capture jobs; accepts manual terminal failed drafts after explicit current-image review, never conflict.
POST /v1/capture/tasks/{id}/images (multipart front/back, Idempotency-Key UUID) -> replacement CaptureTask; original cancelled and linked.
CaptureTask extends CaptureJob with display_status, attention_reasons:string[], can_retry:boolean, image_content_hashes:{}, history:CaptureTaskEvent[], replacement_job_id:string|null.
CaptureTaskEvent: id:string, occurred_at:string, attempt_count:number, display_status, message:string, source:string.
CaptureTaskPage: items, counts{processing,needs_review,failed,imported,cancelled,total}, total,page,page_size,total_pages.

## Review Focus

Concurrent retry/confirm cannot duplicate or overwrite imported items; events scoped by owner. Original conflict cannot be bypassed by manual form. Missing product means review unless independent subject+number anchors suffice; no fabricated per-face evidence. Existing foreign clients tolerate extra fields/null booleans. Empty state differs from failed request and history stays reachable after new tasks.

## File Boundaries

Backend: capture_tasks.py, capture_api.py, scanner_queue.py, schemas.py, pair_integrity.py, db/migrations/0090_capture_task_history.sql and tests. Shared: packages/card-core/src/capture-tasks.ts, contracts.ts. Web: new capture-tasks-panel.tsx plus app/page.tsx wiring. Mobile: JobsPage.tsx and types/style/tests. Release: version manifests/iOS project and specs verification.

## 留空入库修订计划

沿用现有数据关系，对player姓名、brand名称、card_set产品/年份放宽NULL；不完整的目录行独立创建，避免未知身份误合并。输入/输出模型四核心可空，输出动态identity_missing_fields/identity_review_required；现有字段补全后标记自动更新。保留有值的格式/范围校验。

1. 先失败回归：空白核心字段、未知双面不勾选、图片/成本/历史保留、已知对照、匿名目录隔离、全量列表/详情/搜索、补全流程。
2. 实现nullable迁移0092、schema/repository、自动扫描与确认放行；保持真实冲突/过期hash拦截；未识别主体不进入球员行情，估值/成本等待核心足够。
3. 网页与手机独立移除必填/未知配对阻塞，展示待确认标记与空白显示；复用shared confirmation/recognition合同。
4. 实测针对性回归、两端测试与生产构建；定向提交/推送master，云API/网页和iPhone版本化覆盖更新并回读。
