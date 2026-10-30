# Tasks
- [x] Baseline, repository instructions, isolated branch and design/plan
- [x] T1: Correction service + publication tests and implementation
- [x] T2: Management API tests and implementation
- [x] T3: Table/detail UI tests and implementation
- [x] T4: Browser QA, independent review, verification report

## Decisions / progress
- User authorization and exact delegated scope allow serial execution without another design approval round.
- Native worktree tools returned unavailable errors; manual worktree created successfully, no uncertain worktree operation remains.
- Audit-backed proposals avoid schema migration. Source-local IDs remain source-local. Publication does not modify source evidence.

## Final verification / rulings
- Final Python relevant regression155 passed; web25 passed; typecheck/ruff/build passed. Full unrelated suite not claimed.
- One independent review found4 Important, all fixed in one regression-driven pass; report in verification/independent-review.md and author disposition in verification.md.
- Ruling: automatic official_source_keys publication carries published overlays only; atlas drafts require explicit normal publication. Prevents background ingestion changing manual correction state.
- Ruling: unpublished approval can be rejected after rollback/version advance; published history remains immutable. Latest audit event determines state consistently.
- Ruling: face pairing and reviewed target binding are separate; legacy physical paired state is retained, new pair_binding_status/source mappings control the UI audit claim.
- Previously deferred residuals were explicitly reopened and completed in F1/F2 below; see metadata-lifecycle-verification.md.
- Keep local branch/worktree for independent release review; no deployment/push/merge.

## Follow-up: identity metadata and publication history (authorized 2026-10-30)
- [x] F1: Map effective identity/filter/source/pricing dependencies; RED tests for serial/autograph/section changes and original evidence preservation; recompute effective metadata, label original assertions, explicitly mark price grouping not recomputed.
- [x] F2: RED tests for submit/review/publish/carry/rollback/remove/restore/stale-draft history; derive per-proposal state from immutable version membership and all audit events; display exact versions/actors/time/reasons without rewriting evidence or adding production schema.
- [x] F3: Related backend/web/type/build and real key-flow QA; independent narrow review of F1/F2, fixes and report. Wait for actual final readonly adapter before integration; no deploy/push/merge.

- Follow-up verified162 backend /28 web, typecheck/ruff/build pass; independent narrow review completed. Legacy transaction-time ambiguity fixed with retained raw timestamps and visible uncertainty, not rewritten history.
- [x] F4: Align to actual deployed readonly schema7/indexes, run retained SQL plan guards and full-snapshot35-response contract/resource replay.

- Final alignment:168 backend /28 web; cold and warm35 recorded deployed contract cases each zero differences, complete565 products/2,169,016 facts/831 media/14 pairs, issues0. Resource receipts and release gates in deployed-alignment-verification.md.

- [x] 安全收尾：真实会话绑定旧管理actor/发布/回滚/bootstrap/revalue审计，保持数据库程序发布流程。
- [x] 准备兼容API源覆盖和实际web冻结源最小diff；新解释器验证发布/再次发布及未发布提案保留。
- [x] 大历史负载、相关回归、真实密码登录浏览器流程和独立安全复核；证据见security-rollback-verification.md。
- [ ] 后续获准后才执行候选镜像构建、目标环境隔离验收、兼容恢复演练和实际镜像切换；本任务未开始。
