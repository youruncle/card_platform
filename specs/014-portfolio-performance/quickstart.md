# 验证指南

1. 在根目录执行 `.venv/bin/pytest -q apps/api/tests/test_portfolio_read_scope.py apps/api/tests/test_portfolio_search_pushdown.py apps/api/tests/test_image_upload_concurrency.py`。
2. 在 apps/web 执行 `node --import tsx --test tests/dashboard-refresh.test.cjs tests/portfolio-sorting.test.cjs tests/item-image-upload.test.cjs` 和 `npm run typecheck`。
3. 在完全隔离、已迁移 PostgreSQL 上设置 TEST_DATABASE_URL，执行 test_portfolio_performance_postgres.py、test_portfolio_search_postgres.py、test_portfolio_sorting_postgres.py；不得将生产库作为测试库。
4. 浏览器模拟延迟辅助接口，确认列表/详情可先显示、失败可重试、关闭及切卡丢弃旧响应。
5. test_portfolio_performance_postgres.py 中的 120 张合成卡对照输出本地详情耗时中位数，不作线上性能承诺。

6. 对新索引执行 `TEST_DATABASE_URL=... .venv/bin/pytest -q -s apps/api/tests/test_portfolio_indexes_postgres.py`，验证迁移重复执行及自然执行计划。
7. 隔离 Web 构建后，以 PERFORMANCE_WEB_DIR 指向副本、NODE_PATH 指向包含 Playwright 的依赖目录、PERFORMANCE_BROWSER_CHANNEL=chrome，运行 `node --test tests/portfolio-performance-browser.cjs`。接口拦截为固定测试数据，不使用真实用户账户。
