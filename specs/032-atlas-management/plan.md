# Atlas Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; serial implementation with one final independent review.

**Goal:** 图鉴列表/详情和可追溯的纠错提案审核。
**Architecture:** 审计事件→审核草稿→不可变快照覆盖→SQLite派生目录；复用现有发布/回滚。
**Tech Stack:** Next.js 15, React 19, TypeScript, FastAPI, psycopg/PostgreSQL。
**Spec:** specs/032-atlas-management/spec.md

## Global Constraints
原工作区和另一发布任务只读；不部署/push/merge；不修改收藏账本；示例不混入真实页；沿用登录权限；无新增生产schema。

## Review Focus
- 伪造 actor、无角色、只读用户不得写入；新路由身份来自会话。
- 双击/网络响应丢失使用同一幂等键且内容不匹配409。
- 发布水位或同目标已审草稿改变须409，不覆盖旧身份。
- 缺图/关键未知/来源冲突不得被误标全市场确认。
- 返回/取消/手机详情/延迟响应不丢筛选也不重复写入。

### Task 1: Correction service and publication
Files: knowledge_atlas_corrections.py, knowledge_repository.py, knowledge_browser.py; tests/test_knowledge_atlas_corrections.py.
Interfaces: fingerprint(card), apply_corrections(db, snapshot); CorrectionRepository list/submit/review; snapshot atlas_corrections.
- [x] Write failing service and publication tests; run RED.
- [x] Implement audit-backed proposals, validation, locks, idempotency and snapshot overlay.
- [x] Run isolated PostgreSQL and browser regression GREEN.

### Task 2: Management API
Files: knowledge_atlas_api.py, knowledge_api.py; tests/test_knowledge_atlas_api.py.
Interfaces: GET /atlas/{summary,cards,detail,permissions,proposals}; POST /atlas/proposals; POST /atlas/proposals/{id}/review. Query card_id avoids path separator ambiguity.
- [x] Write API tests for filters, authentication, roles and conflicts; run RED.
- [x] Implement bounded queries, capabilities from session and role validation.
- [x] Run API and related regression GREEN.

### Task 3: Table and detail UI
Files: components/knowledge-atlas-manager.tsx, lib/knowledge-atlas.ts, app/knowledge/manage/page.tsx, manage/legacy/page.tsx, atlas-manager.css; tests/knowledge-atlas-manager.test.cjs.
Interfaces: typed management API client and URL filters; proposal payload fingerprint/version/reason/evidence/changes.
- [x] Write interaction tests for filters/back/cancel/preview/idempotency/read-only/errors; run RED.
- [x] Implement responsive table/detail, source/related/image views, proposals/reviews and legacy entry.
- [x] Run web targeted regression/typecheck and build checks GREEN.

### Task 4: End-to-end validation and independent review
Files: verification.md and verification artifacts.
- [x] Browser QA desktop/mobile with isolated test server, screenshots; real frozen card read QA separately.
- [x] One independent whole-change review; fix important issues with regression tests.
- [x] Record exact tested scope, dependencies, remaining release requirements; do not deploy.

### Task 5: Session authorization and compatible rollback safety
See security-rollback-plan.md. Parent explicitly authorized local fixes and independent safety review; no deployment or production writes.
- [x] Reproduce viewer publisher-impersonation and bootstrap impersonation with real auth sessions.
- [x] Bind all legacy mutation actors to the authenticated session; preserve direct DB automation.
- [x] Prepare exact-source compatible API overlay and retain current web source; no image build.
- [x] Test replay across compatible API / frontend-only rollback and history load.
- [x] Related regressions plus independent safety review; record release conditions.
