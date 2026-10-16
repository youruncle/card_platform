# Validation
1. 应用db/migrations/0081_player_price_index.sql，启动API（PLAYER_PRICE_WORKER_ENABLED=true）。
2. 登录打开/player-prices，查看数据/排队状态；请求更新；返回自己的球员。
3. 演示预览仅前端，明显标注，查看涨幅/跌幅排序与展开证据。
4. 单测：.venv/bin/python -m pytest apps/api/tests/test_player_prices.py；前端cd apps/web && node --import tsx --test tests/player-prices.test.cjs；npm run typecheck。
5. PostgreSQL回归使用TEST_DATABASE_URL，仅测试事务回滚。真实样本回放记录数量、可确认记录、拒绝原因，不能冒称全量市场。
