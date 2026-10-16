# 验证记录

2026-10-04 用户批准方案后实现。新增迁移0076，旧记录默认held；本地数据库已应用迁移，API/Web已重启并通过健康检查。实际Chrome已登录页面刷新后确认状态筛选与“更多→标记为已卖出”可见；未改动任何真实卡片状态。

## 行为验证

独立PostgreSQL测试覆盖持有→卖出→恢复、重复设置幂等、账户隔离、回收站不可修改、按状态筛选、真实交易收益保留、历史交易和估值不变。无买入记录的卡片仍算持有。已卖出返回当前数量/成本0、未实现盈亏空，并排除持仓新闻关注及风险提示。仅状态筛选走直接SQL，避免加载大体积知识快照。

前端页面交互测试实际渲染Dashboard并点击状态按钮，验证PATCH的状态、卡片显示与首页市值刷新，以及恢复持有流程；组件测试涵盖保存中禁用、失败提示；筛选测试验证sold条件。

## 检查结果

- 后端相关回归（独立数据库，holding_status/portfolio/news_alerts/valuation_synthesis/codex_joint）：159通过。
- 状态筛选SQL优化后单独再跑数据库完整状态往返：1通过。
- 前端全部测试：106通过。TypeScript类型检查及生产构建通过。
- diff空白检查通过。
- 曾执行后端全量：157370通过、1跳过，新增test_sold_restore_and_owner_isolation因测试夹具缺少transacted_at失败；已修正夹具并在上述相关回归通过。没有在修正后重复全量，因此不宣称最终全量绿。
- 一次并行针对性测试遇到全量测试清理其临时数据库，造成4个数据库连接失败（news_alerts_postgres、portfolio_codex_postgres、valuation_synthesis_postgres、codex_joint_postgres）；改为独立生命周期运行后159项全部通过。
- 既有FastAPI/Starlette测试依赖弃用提示仍存在，非本功能错误。
