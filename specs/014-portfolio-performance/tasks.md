# 任务清单

- [x] T001 [US1] 添加查询范围/筛选下推失败测试 apps/api/tests/test_portfolio_read_scope.py、test_portfolio_search_pushdown.py
- [x] T002 [US1] 实现流水范围/参数化筛选 apps/api/src/card_platform/repository.py、portfolio_search.py
- [x] T003 [US1] 添加辅助挂起/失败和汇总复用 DOM 测试 apps/web/tests/dashboard-refresh.test.cjs
- [x] T004 [US1] 独立列表/汇总/计数及版本保护 apps/web/lib/api.ts、app/page.tsx
- [x] T005 [US2] 添加渐进详情与过期请求回归 apps/web/tests/dashboard-refresh.test.cjs
- [x] T006 [US2] 实现渐进加载/错误重试 apps/web/app/page.tsx
- [x] T007 [US3] 添加线程与上传并发/失败测试 apps/api/tests/test_image_upload_concurrency.py、apps/web/tests/item-image-upload.test.cjs
- [x] T008 [US3] 实现线程处理和双面上传 apps/api/src/card_platform/image_service.py、apps/web/lib/item-image-upload.ts、app/page.tsx
- [x] T009 运行前后端/隔离 PostgreSQL/类型检查，记录 specs/014-portfolio-performance/verification.md

依赖：T001→T002，T003→T004→T005→T006，T007→T008；T009 最后。按顺序在当前工作目录实施，共用此计划。

- [x] T010 [US1] 审计实际数据库已有索引，补齐热点迁移 db/migrations/0080_portfolio_read_indexes.sql
- [x] T011 [US1] 验证迁移幂等性及自然执行计划 apps/api/tests/test_portfolio_indexes_postgres.py
- [x] T012 [US1] 独立审查发现的会话过期数据清理回归 apps/web/tests/dashboard-refresh.test.cjs、app/page.tsx
- [x] T013 [US2] 构建副本及真实浏览器渐进加载验证 apps/web/tests/portfolio-performance-browser.cjs
