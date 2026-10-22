# Verification: 入库自动获取成本

2026-10-17，北京时间。

## Implemented
共用PortfolioRepository在创建无purchase新卡时同事务写入每卡唯一队列；手机确认、网页创建、scanner/import沿用该入口。成本worker顺序处理自动任务及现有手工批任务；按owner序列化，行锁SKIP LOCKED避免重复领取。进程取消回滚保持queued；已有交易二次检查不覆盖；refresh=False。自动任务有单次总超时240秒、搜索150秒、最多3次处理尝试，异常延后60秒再试；账号需验证停止，不自动发送短信。任务结果有所有者鉴权GET。

## Evidence
- 首轮新增5项测试在实现前失败：缺少队列表/模块，证明原有入库不排队。
- 成本冷却状态回归先失败，再修复网络错误路径，使账户cooldown保持。代码审查另发现PostgreSQL now()为事务起始时间，会吞掉慢查询后的退避；增加1秒模拟慢查询验证，先失败（剩余58.98秒）再使用clock_timestamp()修复，保证结束后再等60秒。
- 最终运行 `bash scripts/test-mobile.sh apps/api/tests/test_auto_purchase_cost_postgres.py apps/api/tests/test_purchase_cost_postgres.py apps/api/tests/test_mobile_postgres.py apps/api/tests/test_scanner_queue.py apps/api/tests/test_cardhobby_account.py apps/api/tests/test_cardhobby_account_postgres.py`：108 passed；1个现有Starlette/httpx弃用警告。
- 自动成本专属16项：排队与读取权限、有采购不排队、复用搜索解析3种状态、真实服务唯一匹配写入及重复投递、无结果/多候选/不完整/人工成本/需验证/3次网络失败、事务回滚、冷却保留、双worker及查询期间人工录入、取消后恢复。
- 手机原有确认入库幂等测试增加同一卡只产生一个自动成本任务断言。
- git diff --check通过。

## Deployment and limits
未部署云端、未改真实卡库、未执行真实卡淘或模型联网匹配；不代表真实订单召回率或准确率。客户端代码无本轮功能变更，不重复运行浏览器或iOS签名构建；后端全量测试未运行。
部署顺序：应用0082_auto_purchase_cost.sql迁移→发布API代码→确认PURCHASE_COST_WORKER_ENABLED开启；既有手机/网页自动受益，无需安装新包。只处理发布后新入库记录，不回填历史卡库。自动查找依赖已经保存的卡淘账号；未绑定/需验证需在现有网页入口处理。无结果等业务终态保留人工查询，不无限循环。
运行限制：每次外部查找占用一条数据库连接和该用户成本锁，最多240秒；入库请求不等待网络。停止worker可停止领取；安全代码回退保留队列表和已写入证据，不回滚真实交易。

## Review
独立代码审查确认重试退避计时P2问题，已修复并回归；未发现其他明确的权限、人工成本覆盖或锁序问题。
