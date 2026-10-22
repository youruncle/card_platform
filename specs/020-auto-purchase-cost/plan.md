# Implementation Plan: 入库自动获取成本

## Technical Context
Python/FastAPI/PostgreSQL；复用 PortfolioRepository.create_item、PurchaseCostService、CardHobbyAccountService；客户端不新增触发请求。

## Constitution Check
保留两端架构与未提交工作；订单证据沿用现有审核；按owner隔离；成本未知不猜测。不更改估值逻辑。

## Design
0082迁移新增每卡唯一持久队列，与入库同事务写入；有purchase则跳过。成本worker每轮处理一个自动任务和原手工批任务，避免挤占原任务。数据库行锁SKIP LOCKED避免重复领取，事务回滚保证进程退出后恢复。自动任务按现有owner锁序列化成本确认，网络操作总时间有上限；网络失败最多3次、延后重试；登录验证异常终止并保留账号状态。读取搜索计划后调用已保存账号搜索，再复用resolve_search的唯一证据确认。全过程refresh=False，已有交易跳过。

状态保存在队列并提供owner鉴权GET；无新增前端触发或旧客户端要求。外部HTTP与模型仅在测试中替身，明确不代表真实订单匹配准确率。部署需先迁移再更新API，worker_enabled为既有总开关。

## Structure
- db/migrations/0082_auto_purchase_cost.sql
- apps/api/src/card_platform/auto_purchase_cost.py
- apps/api/src/card_platform/repository.py
- apps/api/src/card_platform/purchase_cost_service.py
- apps/api/src/card_platform/purchase_cost_api.py
- apps/api/tests/test_auto_purchase_cost_postgres.py

## Validation
先写隔离PG集成测试并观察失败，再最小实现；回归成本、手机、scanner与账户相关测试。两端走同一仓储不加重复客户端任务。仅本地实现，未授权在本轮修改云端真实卡库。
