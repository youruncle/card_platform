# Market skill performance validation — 2026-11-02

## Scope and delivered changes

User authorized repeated worthwhile speed improvements and skill publication. Work was isolated on codex/market-skill-speed; unrelated auction changes in the primary checkout were preserved. The existing six tool names remain, with optional backward-compatible parameters:

- start: optional keyword combines target upload and first search; response_mode=compact; parallel target downloads with stable input ordering. Idempotency binds keyword but excludes presentation mode, and no-keyword requests retain legacy hashes.
- search/get/review: compact views and summary_only; query-bound candidate pagination via next_page_args/query_id; candidate/review pages align. Full original captures remain accessible; all captured candidates still require decisions.
- inspect: image_indexes up to4, single detail snapshot, parallel original-image downloads, labeled inline images, one final evidence digest. Successful images survive partial download failure; retry only missing valid indexes. Single-image interface remains compatible.
- performance: per-operation elapsed/stage measurements and cache-hit information. These do not measure model reasoning, connector transit or total chat latency.
- skill: same-chat successful capability-check reuse, exact-first bounded searches, compact and batch paths when schema supports them, old-schema fallback, no redundant report get after a successful review response.

The server valuation implementation was unchanged: original target/candidate captures, identity and comparable condition, current review digest, physical-card deduplication, at least3 independent sellers within180 days. No inventory, cost, transaction, knowledge or formal valuation publication.

## Regression and independent checks

Baseline20 related tests passed. New tests first failed on missing schemas; cursor test then failed before query-bound paging. Independent review found running/legacy queries could fall back to unrelated run candidates; regression reproduced120vs60 candidates, then passed after membership reconstruction from SHA-verified original response. Running/failed queries without membership return an empty selection.

Final related suite:91 passed,1 known baseline deselected,1 third-party deprecation warning. The deselected formal-research expectation (`blocked` versus `insufficient_evidence`) was reproduced unchanged with the original HEAD market module; it was not altered or counted as passing. Ruff, diff check and skill package validation passed. Positive valuation preservation was tested with a controlled three-seller fixture; a real positive non-null valuation remains unverified.

Independent skill scenario: one already-verified chat with one two-image candidate uses combined start→batch inspect→review (3 core calls); title rejects use search/get digests and all candidates receive decisions. Legacy schemas use only supported arguments. Failure, missing gallery indexes and expired image digests preserve evidence limits. These are behavioral scenarios, not end-to-end timing measurements.

## Production release

API image: card-platform-api:0.6.10-20261102-market-speed1. Only mcp/market_sales.py was overlaid onto the actual latest running image. Installed244 and source243 Python files were independently compared;7 pre-existing installed/source differences in knowledge modules were retained exactly. The initial candidate was rejected before activation because the older release verifier assumed those two trees identical; the verifier was corrected to compare each against its own captured baseline.

After activation: healthy, same29 tools, worker=true, DeepSeek explicit thinking disabled, independent Bailian search, environment/mounts/runtime user unchanged. No migration and no unrelated web/mobile binary release were needed.

Personal skill and independent0.2.0 zip generated. Original cloud skill ID6ae60c10f9bc8191937757ad77852802 was updated in place; editor reload and public detail both showed the new body. Saved body equals published body ignoring whitespace. Plugin tools refreshed in official settings; no access/permission changes.

## Real production measurements

These are direct engineering handler checks against real owner-scoped captures and live CardHobby requests, separate from the web attachment acceptance below. Private responses and release logs stay in .runtime/market-speed-20261102/.

| Measurement | Result |
|---|---:|
| Same existing457-candidate run, full report view JSON |73,794 bytes|
| Same run, summary_only JSON |3,667 bytes (95.03% smaller)|
| Full/summary server get time, one run |55.88ms /24.22ms|
| New combined start+one real source search, no target attachment |553.99ms|
| Identical idempotent combined retry |9.80ms; same run,1 query, no new source search|
| Barnes detail+first original image, uncached |714.85ms|
| Cached image batch [0,1] |5.22ms; index0 returned, index1 correctly outside gallery, no download|
| Existing Curry listing batch [0,1] |2 inline images returned, no failed indexes; gallery4;423.76ms,1 additional download|

Barnes old single-image and new batch returned identical original bytes (SHA256185be64097247c4b903def224bff71f0531a2cf2e841d94ebf7e9f3886702270). Both reports retained null estimate and original review progress. Cached versus uncached times are not presented as an optimization speedup. Full versus summary measures a deliberately narrower response; evidence remains in the original run.

Current native client still had the older schema in this turn. Its unchanged get call succeeded against the new API (4.59ms server time), proving compatibility; it was not falsely claimed to exercise new parameters.

## Web attachment acceptance

Completed in a fresh chat explicitly selecting the published skill and refreshed plugin, with the user's two original screenshots uploaded again. Scope was the same Barnes keyword first page. The UI activity list contained five calls: capabilities, profile, combined start/search, batch inspect, compact review. There was no separate search or redundant report get. The model reported combined start2.64s, inspect1.43s and review0.009s server time; new parameters were actually accepted.

The UI recorded54 seconds, versus80 seconds in the prior same-card/first-page run (32.5% lower in this single comparison). This is the frontend's displayed duration, not a precisely sampled click-to-final-byte wall clock or a general latency percentile. The observer first read the completed result later; that polling delay is not assigned to model processing.

A second independent new-run live search in the same chat reused the successful capability checks. The expanded UI activity list confirmed exactly3 core calls (combined start/search, batch inspect, review), with no repeated profile/capability check or report get. It again uploaded the same actual target images, performed fresh source search (search_cache_hit=false), inspected and saved1/1 uncertain review, and returned null estimate. UI duration47 seconds; server combined start2.619s, inspect0.774s, review0.008s. Both runs returned target hashes48c9143dd2df3b3815b706063d13bc930d9a3467123d4b9fbb31d986365f7dcd and29e992774230113d137b81ab9736eb1326f6b97e5ed42f64ea4433b7833b9f03, exactly matching the original PNGs.

Cross-client original-schema get recovered each persisted report:1 query,1 current review,0 pending/0 stale,0/3 eligible independent sellers, estimate=null, ledger_modified=false, valuation_published=false. The single candidate image gallery was1; requested index1 correctly reported missing and was not retried. No missing back, parallel, numbering or condition fields were fabricated. Private readbacks: web-final-readback.json and web-hot-readback.json in the runtime directory.

## Remaining limits

No global latency/accuracy claim follows from one sample. No iPhone/Android performance acceptance; no real positive non-null valuation test. The older eBay source limitation remains irrelevant to a CardHobby-only speed test and cannot be treated as a completed dual-market valuation.
