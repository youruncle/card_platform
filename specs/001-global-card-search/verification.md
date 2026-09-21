# Execution record

Plan: specs/001-global-card-search/plan.md

采用 Spec Kit 单份规格与清单，Superpowers TDD、验证与最终独立审查。用户指定在原工作区保留全部修改，因此不创建 worktree、不提交混杂改动、不清理文件。AGENTS.md 要求持续执行已授权范围，无关键需求缺口不重复确认。

Preflight: 搜索过滤模型由后台与前端共同遵守；请求参数为可选 JSON，旧分页路径兼容。使用本位币金额与正式估值定义，避免影子估值混入。

## Baseline and RED/GREEN

Baseline: 1815 passed, 55 skipped. Direct `pytest` initially failed to import root-level `scripts`; using `.venv/bin/python -m pytest` fixes the runner path without changing production code.

Before implementation, real API tests showed search being ignored and malformed filters accepted (5 failures). New module tests initially failed import. Implemented owner-scoped pre-pagination search and strict validation, then observed green results. Worker phases identifying/searching/calculating initially failed the running filter (3 failures), then passed after aggregation.

Independent review: one read-only reviewer inspected the bounded search scope. No Critical findings. Fixed all findings in one pass:

- Roster synonyms symmetrically merged two solo players. Added two failing identity-discovery regressions, then made roster expansion directional.
- A failed new search from page 3 rolled the background retry back to page 3. Reset the page ref before submitting a new query.
- Failed searches claimed zero matches. Added a failing dashboard test, separate request failure state, suppressed successful empty/count displays and provided retry with the current conditions. Targeted web suite: 23 passed.

One test assertion originally compared a DOM node with null using deep diagnostic output and was SIGKILLed; replaced it with a concise boolean assertion and observed the intended failure. No application change was made for this test runner issue.

## Latest verification

- Ruff `check apps/api/src apps/api/tests`: passed.
- Full non-PostgreSQL invocation: 1871 passed, 61 skipped; skipped DB tests run separately below.
- Frontend full CJS suite: 80 passed; includes cost queue / batch / page refresh regressions.
- TypeScript `tsc --noEmit`: passed.
- Next production build and standalone static/public asset sync: passed.
- Isolated PostgreSQL full suite: 1931 passed, 1 skipped. Extra financial/grade/serial/deletion test initially omitted the required DELETE request body (422); corrected the fixture to use the existing API contract, no production workaround.
- Existing warning: Starlette/httpx TestClient deprecation.
- Final repeated PostgreSQL run after formatting: 1931 passed, 1 skipped (optional private flatbed manifest source not configured), 77.58 s. Search/pagination targeted final run: 57 passed. `git diff --check`: clean.

## Real account, read-only

Database transaction was explicitly read-only. KEVIN DURANT / 凯文杜兰特 / 杜兰特 / 凯文 杜兰特 each returned the same 4 cards; ID 519 returned 1; 教堂 returned 2. Lightweight projection search timings were 24–61 ms. Subsequent measurements including full result-page projection were 94 / 49 / 54 / 24 / 46 ms respectively for KEVIN DURANT / 凯文杜兰特 / 杜兰特 / 519 / 教堂. These are backend measurements, not end-to-end browser latency. Fuzzy `durent` returned 5 (can also match Duren); fuzzy discovery intentionally is not identity proof.

No real card identities, orders, costs, valuations, or knowledge entries were changed by this feature. Tests create invented records only in the isolated temporary PostgreSQL database.

## Boundaries / risks

Aliases come from the published knowledge snapshot and shared vocabulary. Unknown Chinese translations are not guessed. Fuzzy Latin one-edit matching can broaden short names; use advanced fields and exact IDs to narrow results. Search scans lightweight projections of the current owner (not all accounts), appropriate for the measured ~519-card collection; substantially larger inventories may need an indexed search projection. Asset summary metrics intentionally remain whole-account totals rather than search totals.

## Deployment and browser verification

Built production standalone output, synced assets, restarted only the verified local API/Web listeners. API PID 40825 on 127.0.0.1:8000; Web PID 40837 on 127.0.0.1:3000. `/health` = ok; `/health/ready` = ready; homepage HTTP 200. No database migration needed. Existing standalone service setup preserved (no LaunchAgent permission changes).

Native Chrome verification on the existing authenticated session:

- Search appears directly under 核心持仓 and above the table, no topbar search; screenshot inspected at desktop width.
- 杜兰特 and KEVIN DURANT both return IDs 519 / 233 / 224 / 65.
- KEVIN DURANT AND brand Topps returns only ID 519; removable condition chips visible.
- Advanced panel opens with the specified fields; collapse preserves applied filters.
- A no-match query displays the proper empty result state.
- Clear filters restores all 519 cards, page 1 of 18; deployed CSS returned HTTP 200.

The browser extension automation connection timed out; native Chrome accessibility controls succeeded. Use `setValue` to avoid clipboard/input-method interference encountered with `paste`/`typeText`. No developer-console scripts or private session extraction were used. Narrow-screen CSS breakpoints were inspected, but a separate physical mobile-width screenshot was not obtained.

## Spec Kit convergence

Checked 7 FRs, 5 SCs, 9 story acceptance scenarios, 5 implementation decisions (existing stack, optional API parameter, owner projection before pagination, request-version safety, no new runtime dependency/migration), and all 5 constitution principles. No remaining buildable gap in the approved scope; no convergence tasks appended. SC-005 performance evidence measures server search and page projection rather than browser/network latency. UI narrow-width manual verification remains an explicit coverage limitation, not a claimed performed test.

Superpowers finish: retained the existing main workspace and all unrelated uncommitted changes per the user's explicit instructions; no commit, merge, push, worktree cleanup or user-file deletion.
