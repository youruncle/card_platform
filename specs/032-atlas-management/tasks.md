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
- Deferred Minor: application version/current rollback lifecycle timeline; original-source serial/autograph metadata versus corrected effective field metadata. Detailed costs and scope in verification.md.
- Keep local branch/worktree for independent release review; no deployment/push/merge.
