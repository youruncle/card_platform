# Tasks: DeepSeek official web research

## Setup and foundation

- [x] T001 Inspect current production, preserve existing DeepSeek visual migration and create isolated branch.
- [x] T002 Execute real read-only Search/Fetch feasibility probes and record partial site failures.
- [x] T003 Maintain one spec/plan/tasks set and review constitution; no active extension hooks.
- [x] T004 Add failing collector/config boundary tests in apps/api/tests/test_deepseek_web.py.
- [x] T005 Implement bounded official research collector in apps/api/src/card_platform/deepseek_web.py and safe config resolution.

## US1: Card catalog

- [x] T006 [US1] Test and replace card_search_service.py Qwen search with captured DeepSeek evidence; retain source/image verification.

## US2: Market research

- [x] T007 [US2] Test and replace ingestion/cardhobby.py discovery fallback, preserving first-party sale gates.
- [x] T008 [US2] Test and replace valuation_worker.py generic research, rejecting unobserved sources and retaining captures.
- [x] T009 [US2] Run related regression suites and real read-only product research; record limitations in validation.md.

## US3: Release

- [x] T010 [US3] Fresh code review, fixes, current production baseline/source guard and release backup.
- [x] T011 [US3] Publish API/web, migrate only necessary search settings, verify official models, explicit non-thinking, worker=true and health.
- [ ] T012 [US3] Build/sign/cover-install new personal iPhone app, verify installed version and launch/process.
- [x] T013 [US3] Public pages/assets/runtime checks and spec convergence; record final deployment evidence.

## Dependencies and execution

T001–T005 precede consumers; T006/T007/T008 share collector interface and are implemented inline. Read-only integration research can proceed independently. T009–T010 gate activation; API/web release and iPhone packaging use the same verified deployment baseline. Existing business data is not a test fixture.

## Phase 1: Convergence

- [ ] T014 [US3] Resume the remaining T012 steps when the paired physical iPhone is connected and unlocked: reread installed version to prevent downgrade, cover-install the reviewed personal-signature bundle 1.1.12 (16), verify installed version and launch/process state, and append the actual receipt to specs/027-deepseek-web-research/deployment.md per FR-009 / US3 (partial, HIGH). Code, signing and cloud publication are ready; CoreDevice currently reports the device unavailable. Continuation metadata: private `.runtime/deepseek-web-research-20261020/mobile-followup.json`.
