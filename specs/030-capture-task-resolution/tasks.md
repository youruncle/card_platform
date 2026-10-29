# Tasks

- [x] T001 Define approved scope, API/client contracts and preserve dirty baseline.
- [x] T002 Red/green gate normalization, key identity readiness and nullable unknown persistence.
- [x] T003 Red/green owner-scoped task pages/counts/detail/history, manual retry/confirm/replacement and migration.
- [x] T004 Web persistent task entry, explicit states/errors, pages, images, confirmation/retry/replacement/history and tests.
- [x] T005 Mobile unified task center with matching actions and tests.
- [x] T006 Independent code review, disposable PG integration, both client suites/builds and real-sample replay.
- [ ] T007 Task-only commits; merge/push master with existing changes preserved.
- [ ] T008 Cloud migration/API/web release, iPhone versioned install/startup and final readback.

Implementation evidence: owner-scoped disposable PostgreSQL integration, 133 focused backend regressions plus final 14 task tests, mobile 147 tests, web 207 tests; repeated web tests against isolated task-only page recorded in verification. Independent pair-gate review found no remaining blocker. Release remains in progress until cloud and device readback.
