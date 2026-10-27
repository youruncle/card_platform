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

## 2026-10-28 账号阻塞提醒修复计划

沿用FastAPI和两端React，无新依赖、无数据库迁移。继续在当前工作区，仅修改本任务文件，保留其他未提交工作。

1. 在`test_auto_purchase_cost_postgres.py`添加GET `/v1/purchase-cost/automatic/attention`鉴权、仅本用户有效阻塞计数及GET无副作用测试；在账号PG测试添加成功登录后只恢复账号阻塞任务、保留已有交易的测试，先确认失败。
2. 在`auto_purchase_cost.py`实现安全汇总与受限恢复；在`purchase_cost_api.py`提供GET；`cardhobby_account.py`保存成功会话后重新排队受阻任务。不会新增自动短信或绕过验证，不改变worker开关。
3. 在`packages/card-core/src/automatic-cost-attention.ts`实现两端共用的只读轮询与生命周期取消；两端新增提示组件，嵌入已登录根界面，复用各自现有账号表单。组件保存当前挂载账户已提示的notice_key，关闭后保留入口；无跨用户持久存储。
4. 真实组件先红后绿覆盖首次/延迟阻塞、重复轮询、关闭和重新打开、验证流程、卸载迟到响应；运行相关PG、Web与Mobile测试及类型/构建检查。核查任务diff、记录实际结果和未运行项。不在本次修复中直接部署或自动提交短信。
