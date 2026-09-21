# Tasks: 全库搜索

## Setup / Foundation
- [x] T001 保存已批准范围与接口约定于 specs/001-global-card-search/spec.md、plan.md、contracts/search.md。
- [x] T002 建立原始全量回归基线，记录到 specs/001-global-card-search/verification.md。
## US1 / US2: 全库、中文与模糊检索
- [x] T003 [US1] 在 apps/api/tests/test_portfolio_search.py、test_portfolio_search_postgres.py 写全库分页、ID、隔离、非法输入失败测试。
- [x] T004 [US2] 在 apps/api/tests/test_portfolio_search.py 写别名双向、规范化、多词、受限拼写测试。
- [x] T005 [US1] 实现 apps/api/src/card_platform/portfolio_search.py 与 api.py / repository.py 分页集成。
## US3: 属性与界面
- [x] T006 [US3] 补 apps/api/tests/test_portfolio_search.py 全部属性/范围/未知/零值的测试并实现。
- [x] T007 [US3] 在 apps/web/tests/collection-search.test.cjs、dashboard-refresh.test.cjs 写布局、过滤、清空、服务端请求、过期响应测试。
- [x] T008 [US3] 实现 apps/web/components/collection-search.tsx、lib/portfolio-search.ts，接入 app/page.tsx、lib/api.ts、globals.css。
## Cross-cutting
- [x] T009 独立审查相关文件并在 specs/001-global-card-search/verification.md 记录发现及 RED/GREEN 修复。
- [x] T010 运行全量测试、构建、真实只读查询、浏览器和部署健康验证，更新 specs/001-global-card-search/verification.md。

Dependencies: T001 → T002 → T003/T004 → T005/T006 → T007/T008 → T009/T010。后台单测与前端独立控件测试可并行运行；本次主代理顺序实现，仅最终审查交给独立审查代理。
MVP: US1 可独立验证；本次交付仍包含全部已批准故事。
