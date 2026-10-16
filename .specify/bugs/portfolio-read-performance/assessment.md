# 卡库读取性能检查

日期：2026-10-11

## 发现与最小修复

`PortfolioRepository.item_detail` 原先调用不带 ID 过滤的 `list_items`，计算整个账户的卡片投影后再选择目标卡。投影包含估值历史、市场证据、联合估值和持仓计算，工作量随账户卡片数增加。

已将详情投影限定为目标卡 ID；带 ID 过滤的列表同时限定交易流水读取范围。保留 owner_id、未删除状态检查以及原有计算方式。未改动真实数据、前端或其他已有未提交业务修改。

## 其他优化机会（尚未实现）

1. 搜索：`portfolio_search.search_item_ids` 除仅持有状态筛选外，读取账户全部未删除卡片，在 Python 中匹配、排名。可先下推结构化筛选，再评估兼容中文别名的搜索索引。
2. 首次搜索/排序：`portfolio_sort.sorted_page` 在 include_summary=true 时计算全库投影，再过滤候选卡。可拆分列表和汇总，设计账户级版本及失效机制，保持联合估值与排序口径一致。
3. 列表显示：前端 refresh 等待卡库与全账户纠错计数全部完成。可让辅助计数独立加载，保留请求失效与会话隔离保护。
4. 详情显示：openItemDetail 等待详情、纠错、复核全部完成后才显示。可先显示核心详情，渐进加载复核信息，并防止切卡后旧响应覆盖。
5. 录入：handleAddCard 依次创建、上传正面、上传背面、清理队列和刷新卡库。先测量各阶段，再评估合并上传或并发；需检查图片处理及数据库锁，避免直接并发引入一致性问题。

## 验证

新增查询范围回归测试，修复前两项失败，修复后通过。覆盖目标卡过滤、流水范围、完整账户和空 ID 集合。

运行：`.venv/bin/pytest -q apps/api/tests/test_portfolio_read_scope.py apps/api/tests/test_portfolio.py apps/api/tests/test_portfolio_pagination.py apps/api/tests/test_portfolio_sorting.py apps/api/tests/test_portfolio_search.py apps/api/tests/test_portfolio_sorting_postgres.py apps/api/tests/test_portfolio_search_postgres.py`

结果：90 passed、15 skipped、1 个既有 Starlette 弃用警告。PostgreSQL 测试因未配置 TEST_DATABASE_URL 跳过。未运行真实浏览器/生产压测，未测量线上耗时，无法报告提速比例；未部署。

## 后续实施

用户于 2026-10-11 授权继续优化。统一规格和任务位于 specs/014-portfolio-performance；最终验证记录见该目录 verification.md。上文 90 项测试及未运行 PostgreSQL 的描述是首轮检查状态，后续已增加真实隔离 PostgreSQL、Chrome 和热点索引验证。
