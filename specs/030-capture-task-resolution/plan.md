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
