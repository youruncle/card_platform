# Tasks

## Performance iteration 2026-11-02

- [x] T008 RED/GREEN compact responses, aligned candidate/review pagination and timings; old defaults compatible.
- [x] T009 RED/GREEN combined target creation/first search, idempotency and failure recovery.
- [x] T010 RED/GREEN batch original images, bounded concurrency and partial failure recovery.
- [x] T011 Old/new skill behavior, fixed-sample call/bytes/time comparison and validation.
- [x] T012 Independent review, focused regressions, cloud API/original skill release and real client readback.

Ruling: extend six existing tools to avoid a new tool-directory requirement. Old schemas fall back. Baseline search approximately0.95s and web workflow approximately80s measure different spans; do not attribute all difference to model work. Existing unrelated auction edits remain in the primary checkout.

- [x] T001 RED/GREEN deterministic sale normalization, completeness and valuation guards.
- [x] T002 Durable owner-scoped MCP tools, captured images, review binding and DB/transport tests.
- [x] T003 Independent skill, behavior verification and package validation.
- [x] T004 Review, focused regressions, cloud API and migration release with invariant checks.
- [x] T005 Cloud skill installation and actual query/image/report readback; document client limits.
- [x] T006 用用户Barnes正背截图执行真实端到端验证，区分当前聊天工具缺失、锁屏阻塞与服务端验证；记录全部候选决定及历史观察。
- [x] T007 RED/GREEN补充空估值原因、明确审核进度/图片依据，改进能力判断和有卡号的搜索顺序；更新发布并记录仍未验收边界。

### 2026-11-01 真实卡复核状态

T006：工程检索12请求/457候选决定/1张候选实图已完成；解锁后当前Codex真实原图上传及六工具链已通过（限定词第一页1候选，明确空估值），网页独立两附件流程亦通过，跨客户端回读原SHA/1次查询/1条实图审核/空估值一致。标题规则筛选411，45待核，1旧类比，不冒称全部视觉核验。

T007：代码改进、209项回归、独立行为/代码检查和生产API market4发布已完成；真实工具暴露后纠正files双schema说明，本机和云端原skill均更新，云端重开回读正文一致。非null真实估值及iPhone/Android仍未验收。详见real-card-validation-20261101.md。

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

Performance execution ledger: baseline20 tests passed. New schema tests first failed for missing response_mode/keyword/image_indexes. Compact query-cursor test failed before query_id implementation. Review found running/legacy-query selection leakage; new regression failed120vs60 before verified-capture reconstruction fix. Final focused regression91 passed/1 known baseline deselected, warning only; the excluded formal-research test was independently reproduced unchanged using original HEAD market module. Static review and independent behavior fast-path checks completed. Commit6431db6e.

Release acceptance: API market-speed1 healthy; separately verified installed244/source243 files and preserved7 pre-existing tree differences. Worker/vision/search/runtime invariants retained. Original cloud skill updated in place, reloaded body matched, plugin tools refreshed. Real457-candidate report full73794B/summary3667B. Fresh web first-page Barnes run displayed54s (prior80s), same-chat independent repeat47s; both original target SHA match, actual images and1/1 review persisted, no estimate/ledger writes. Full findings and measurement limits: performance-validation-20261102.md.
