# Validation — 2026-10-20

## Scope and baseline

Isolated branch `codex/deepseek-search-fetch` based on committed `c9f5b811` preserves original dirty workspace. Current production API deepseek1 and web deepseek2 were checked directly. All 190 original package Python/JSON files match that baseline; only this feature's nine existing modules plus deepseek_web.py change. No database migration or business test writes.

## Tests

- Collector/config first RED: 15 failures; implementation then 15 passed. Initial consumer RED: 5 failures. Focused integration then 57 passed.
- Fresh review reported three P2 issues. Four regressions were observed failing before fixes: capture loss on SDK error, empty URL-only evidence, cancellation capture loss, and missing targeted site queries. Fixes retain private archives, skip content-free analysis, and actually execute eBay plus two third-party queries.
- Final relevant suite: **336 passed, 27 deselected** in 60.96 seconds. The exact 27 excluded failures were separately reproduced on the unchanged committed baseline: 23 CardHobby matching/query tests, 1 valuation revalidation test, 2 old scheduling mock-signature cases, 1 obsolete pair prompt exact-text assertion. They were not repaired or counted as passed.
- New/related changed files pass Ruff. Existing worker/CardHobby modules retain seven prior Ruff diagnostics (five unused imports, one unused variable, one undefined resolved_identity); all seven reproduced on baseline. Complete API/PostgreSQL suite was not rerun.
- Xcode Release build and strict deep codesign verification pass for personal iPhone candidate **1.1.12 (16)**. All six public resources exactly match the previously verified 1.1.10/1.1.11 assets. Profile includes the existing device and expires 2026-10-22 16:56:53 UTC. Installation/startup are pending physical connection.

## Real read-only research

Initial raw official Search succeeded for checklist/eBay/CardHobby; Fetch succeeded for official docs and a CardHobby page. eBay/TCDB examples returned 404 and Beckett returned 504; these are recorded failures, not zero-sale claims.

A first full product-code probe (no collection or transaction writes) completed catalog and market analysis, with captured sources and actual deepseek-flash wire requests using thinking.type=disabled and no Qwen search flags. Catalog candidates remained unverified where original pages could not support them. No actual recognition accuracy, historical sale completeness or price improvement is claimed. A final probe after review fixes is recorded separately in private receipts.

Private receipts: `.runtime/deepseek-web-research-20261020/`; no keys, account images, orders or backup content are committed.

## Independent review and dispositions

All three P2 findings were fixed with failing regression cases before implementation, followed by the final relevant suite. No unresolved review findings or deferred minors.

Reviewed non-findings: CardHobby model discoveries still cannot enter first-party sale evidence; URL membership is exact except fragments; token usage tracking does not include per-request Search/Fetch fees and is not represented as total cost; existing baseline failures/site limits remain explicitly disclosed. Completed observations survive cancellation in private `.web-research` archives on the persisted scanner volume.

## Final integration and runtime verification

A concurrent cost-evidence release changed four unrelated package modules while the first read-only probe was running; replacing its container terminated the probe (exit 137, no new OOM event). Its committed change `6f41bcd7` was merged unchanged, and the release rebuilt on actual `fusion1`, preserving its cost behavior. New base plus research-focused follow-up: **71 passed**. Final image package contains exactly the expected **191** Python/JSON files.

The corrected standalone candidate probe completed both actual model analyses: catalog 30.51 seconds, market 35.44 seconds, 5/8 captured source pages. All real model requests used deepseek-flash with thinking.type=disabled; eBay, SportsCardsPro and Sports Card Investor queries are in actual tool execution records. Fetch failures remain explicit. No full-card valuation, transaction write or accuracy improvement is claimed.

After activation, all seven configured model roles are deepseek-flash, official endpoints and matching native credentials verified, native non-thinking body verified, worker environment/application settings both true, API ready. Actual running-code Search returned 7 source pages and Fetch succeeded for 3; its capture was saved as mode 0600 in the private persisted scanner volume. Homepage, player-prices and capabilities returned 200; all 14 distinct referenced assets returned 200; anonymous private purchase history returned 401.

The iPhone remains unavailable in CoreDevice, so cover-install, installed-version readback and launch are **not verified**. A signed 1.1.12 (16) bundle and IPA are ready. This is the sole remaining release step; do not report all-platform deployment as complete.
