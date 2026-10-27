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


## 2026-10-28 自动成本账号阻塞提示修复

### 根因与改动
后台自动任务遇到卡淘账号验证时保留 `completed/account_required`，但两端只在人工获取成本时展示账号表单，缺少全局自动任务观察入口。成功登录也未恢复这些已结束的自动任务。

新增有鉴权、禁止缓存的账号阻塞汇总接口，仅返回当前所有者未删除、没有交易的受阻卡数量、提示代码和失败时间。网页与手机在登录后主动检查、前台每15秒检查及恢复焦点时检查，复用现有卡淘登录/验证表单；关闭后保留可重新打开的提醒，同一事件不重复弹出。成功保存已验证会话后，仅恢复该所有者符合上述条件的账号阻塞任务，其他失败及已有交易保持原状。观察请求不登录、不发送短信。

### 验证证据
- API首轮失败证明缺少汇总路由及登录后恢复；新增真实PostgreSQL回归覆盖未登录、账号隔离、排除删除/已有交易、成功与失败登录，以及新失败事件。
- 独立审查发现数量减少导致重复弹窗；API和两端组件先补失败回归，再将数量从提示键去除，并在客户端用最新失败时间与代码去重。删除最新受阻卡导致时间倒退时也不重新弹窗。复核未发现新增明确的Critical/Important问题。
- `CARD_PLATFORM_TEST_PORT=55447 bash scripts/test-mobile.sh apps/api/tests/test_auto_cost_attention_postgres.py apps/api/tests/test_auto_purchase_cost_postgres.py apps/api/tests/test_cardhobby_account.py apps/api/tests/test_cardhobby_account_postgres.py apps/api/tests/test_purchase_cost.py apps/api/tests/test_purchase_cost_postgres.py`：**246 passed**，1个现有Starlette/httpx弃用警告；使用一次性测试数据库。
- 网页 `node --import tsx --test --test-timeout=30000 tests/*.test.cjs`：**196 passed**。
- 手机 `npm test`：**135 passed / 19 files**。
- 网页和手机 `npm run build` 均通过，包含类型检查。
- 实际Chrome加载最终生产构建，分别以1440px网页和390px手机视口验证根组件自动弹窗、关闭后提醒、重新打开、焦点刷新不重复弹窗、登录完成后清除，以及不自动发送短信或调用逐卡搜索。无页面异常与横向溢出；已核看两端弹窗及提醒截图。
- 浏览器脚本、日志及截图：`.runtime/cost-attention-20261028/`。
- `git diff --check` 通过；保留工作区其他任务修改。

### 范围与限制
本轮仅完成代码与本地验证，**尚未发布线上**，未改变线上worker配置或真实卡片成本。Chrome使用虚构账号和拦截的接口响应；未操作真实卡淘登录/验证码，未安装个人iPhone版本，未运行整个API测试集。真实账号恢复后的订单召回和成本结果仍需发布后验证；生产worker必须继续保持 `PURCHASE_COST_WORKER_ENABLED=true`。
