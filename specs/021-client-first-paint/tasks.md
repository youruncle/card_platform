# Tasks: 两端首屏优先

## Setup
- [x] T001 检查首屏依赖并写入 specs/021-client-first-paint/research.md
- [x] T002 完成同目录 spec.md、plan.md 和契约

## US1 首批列表
- [x] T003 [US1] 修改 apps/web/tests/dashboard-refresh.test.cjs 并在 apps/mobile/tests/capture-shared.test.ts 覆盖共用请求参数，先运行失败
- [x] T004 [US1] 修改 packages/card-core/src/sorting.ts、apps/web/lib/api.ts、apps/web/app/page.tsx 和 apps/mobile/src/LibraryPage.tsx，分离列表与汇总
- [x] T005 [US1] 验证 apps/mobile/tests/library-page.test.tsx、后端membership与sorting回归

## US2 按需面板
- [x] T006 [US2] 在 apps/web/app/page.tsx 按需加载非首屏组件并保留加载反馈
- [x] T007 [US2] 在 apps/web/tests/portfolio-performance-browser.cjs 验证悬挂统计、首屏模块与按需面板

## Verification
- [x] T008 运行两端测试、隔离生产构建、浏览器验证，记录于 specs/021-client-first-paint/verification.md

## Dependencies
T001→T002→T003→T004→T005→T006→T007→T008；顺序实现。共用构造器测试与Web失败用例可独立准备，不另建计划。
