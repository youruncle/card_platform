# Tasks

- [x] T001 RED/GREEN deterministic sale normalization, completeness and valuation guards.
- [x] T002 Durable owner-scoped MCP tools, captured images, review binding and DB/transport tests.
- [x] T003 Independent skill, behavior verification and package validation.
- [x] T004 Review, focused regressions, cloud API and migration release with invariant checks.
- [x] T005 Cloud skill installation and actual query/image/report readback; document client limits.
- [ ] T006 用用户Barnes正背截图执行真实端到端验证，区分当前聊天工具缺失、锁屏阻塞与服务端验证；记录全部候选决定及历史观察。
- [ ] T007 RED/GREEN补充空估值原因、明确审核进度/图片依据，改进能力判断和有卡号的搜索顺序；更新发布并记录仍未验收边界。

### 2026-11-01 真实卡复核状态

T006：服务端真实原图/12请求/457候选决定/1张实际候选图/报告回读已完成；当前聊天六新工具缺schema，网页附件因系统锁屏暂停，客户端端到端仍未完成。标题规则筛选411，45待核，1旧类比，不冒称全部视觉核验。

T007：代码及本机skill改进、209项回归、独立行为/代码检查和生产API market4发布已完成；云端账号skill文本更新待Mac手动解锁。两任务保持未勾选直到其客户端/云端skill部分真正完成。详见real-card-validation-20261101.md。

## Execution ledger

- Approved design: prior chat response; user explicitly requested build and cloud release.
- Existing working branch codex/cardhobby-valuation-quality; no tracked edits at start, unrelated untracked artifacts preserved. Work in place to reuse prepared environment and avoid unnecessary checkout changes.
- Baseline reviewer correctly withheld estimate but unnecessarily required payment confirmation before using public auction observations; skill must distinguish public hammer evidence from payment receipts.
- One shared spec/plan/tasks per project instructions; no second Superpowers plan or approval cycle.

- RED/GREEN: normalization, exact identity gates, leading-zero serial dedup, missing numbered serial, durable SQL ownership/idempotency, actual inline image, stale review, concurrent search and revoked grants.
- Independent reviewer found four issues; failing regressions added then fixed: concurrent detail snapshot mixing, incomplete total pages, mismatched bid count, corrupt capture readback. Fresh static re-review found no remaining release blocker.
- Focused suite: 206 passed; broad suite 333 passed/35 failed; isolated original production source reproduces identical 35 failures (0 new), in existing formal-research/provider expectations.
- Cloud skill installed and visible under installed/created; ID 6ae60c10f9bc8191937757ad77852802. Final API market3 healthy; production query, inline image capture, raw evidence and same-run restoration verified. iPhone/Android and a real non-null target valuation are not claimed.
- Actual ChatGPT skill+plugin session completed public search and same-run image/report recovery; visible card text, 48/99 and material-window observation matched independently inspected image. Missing back/year/target image were kept unknown; no price or ledger writes.
