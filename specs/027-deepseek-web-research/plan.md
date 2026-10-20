# DeepSeek Web Research Implementation Plan

> **For agentic workers:** Use Superpowers executing-plans and test-driven-development against this Spec Kit plan and tasks; do not create a second plan.

**Goal:** Replace remaining Qwen research with DeepSeek Search/Fetch and grounded non-thinking analysis, then publish all three clients/services.
**Architecture:** A bounded HTTP research collector runs real Search and Fetch before existing model analysis. Each consumer retains captures and accepts only observed source URLs. Existing first-party CardHobby retrieval, source/image verification, and financial gates stay authoritative.
**Tech Stack:** Python 3.12, httpx, OpenAI-compatible SDK, FastAPI, PostgreSQL, Next.js, Capacitor/iOS.
**Spec:** [spec.md](spec.md)

## Technical Context

Storage uses existing recognition/job JSON payloads and private capture archives in the persisted scanner volume; no migration. Tests use pytest with HTTP transport fixtures and existing focused regression suites. Production runs Linux containers; iPhone uses existing personal signature. Maximum 4 queries, 8 retained pages, 3 Fetch requests, concurrency 3, 12-second tool timeout, and one retry for transient failures. Each retained body is capped at 6000 characters with a hash and truncation marker. Existing enrichment deadlines remain enforced.

## Global Constraints

- Preserve already deployed visual migration: deepseek-flash and explicit thinking.type=disabled.
- Runtime PURCHASE_COST_WORKER_ENABLED=true, including restart and rollback.
- Only exact official DeepSeek HTTPS endpoints receive DeepSeek credentials; legacy unpaired or non-DeepSeek search configuration fails closed.
- Captured snippets and model statements never prove completed sales; no threshold relaxation or real card/ledger edits.
- Preserve current published web/mobile resources and any newer concurrent release; source hashes guard the base image.

## Constitution Check

Pass before and after design: preserve architecture/work, retain source evidence and uncertainty, verify observable behavior, isolate credentials/accounts, require real market evidence. No violations or schema migration.

## Review Focus

- Empty/malformed results or total outage: no evidence-free model request.
- A fabricated or modified source URL: cannot support an identity or price.
- Wrong provider key/base pairing: no Alibaba credential is sent to DeepSeek.
- Partial Fetch failure: preserve Search capture and failure classification without upgrading evidence grade.
- Concurrent release or newer iPhone version: do not overwrite unrelated changes or downgrade the app.

## Project Structure

- Add apps/api/src/card_platform/deepseek_web.py for bounded Search/Fetch collection and grounded completion.
- Update config.py, card_search_service.py, valuation_worker.py and ingestion/cardhobby.py at the existing search boundaries.
- Add captures to existing result payloads in schemas.py and internal result models.
- Update .env.example and runtime documentation; keep API contracts additive.
- Add tests/test_deepseek_web.py and consumer regressions; adjust the superseded Qwen-isolation expectations in test_deepseek_vision.py.
- Version and package existing mobile/web inputs only after checking deployed versions.

## Task 1: Official research collector and credential routing

**Interfaces:** DeepSeekWebResearch.collect(queries, allowed_sites=()) returns ResearchCapture; complete(client, queries, allowed_sites=(), **kwargs) returns (response or None, capture). ResearchCapture.observed(url) checks captured source membership; as_dict() serializes bounded evidence.

- [ ] Write transport-level tests: actual Search/Fetch paths, Bearer destination, query cap, deduplication/domain/URL filtering, failure/retry policy, body capture, empty results and grounded non-thinking request.
- [ ] Run tests and observe missing-feature failures.
- [ ] Implement minimal collector and official-only credential resolution in config.py.
- [ ] Run focused tests plus existing visual-request regressions; commit only this task's changes.

## Task 2: Ground all three research consumers

**Consumes:** Task 1 collector and capture interface.
**Produces:** Catalog enrichment, CardHobby discovery, and general market research using captured sources and existing schemas/gates.

- [ ] Write failing tests for catalog/market/CardHobby paths, including invented links, partial Fetch failure and empty evidence.
- [ ] Replace Qwen enable_search calls with explicit collection and DeepSeek non-thinking completion; preserve actual queries/captures and all downstream gates.
- [ ] Run relevant catalog, market, first-party CardHobby, vision and purchase-cost regressions; document baseline failures if any.
- [ ] Repeat real read-only searches through product code; preserve captures without writing business records; commit reviewed changes.

## Task 3: Reviewed release and three-platform verification

**Consumes:** Verified Tasks 1 and 2, actual current image IDs and installed iPhone metadata.
**Produces:** Compatible API/web release and newly signed personal iPhone build with verified runtime state.

- [ ] Review branch against spec and actual deployment base; record findings and fixes.
- [ ] Back up current Compose/config and image identity; preserve source baseline and existing data.
- [ ] Build API from current deployed source plus reviewed changes; publish web from current published build inputs. No database migration.
- [ ] Atomically change only required official search settings; activate images and verify all model settings, explicit non-thinking requests, health and worker=true.
- [ ] Build/sign/install newer iPhone version preserving current assets/data, reread installed version and launch/process state.
- [ ] Verify public pages/static assets and runtime Search/Fetch; record actual tests, site limitations, and deployment receipts in validation.md/deployment.md.

## Execution

User explicitly authorized feasibility-dependent replacement and deployment. Execute inline, with read-only integration research and one fresh final review. The shared tasks.md is the only progress checklist. No extra approval for already authorized routine steps.
