# Tasks: 入库自动获取成本

## Phase 1: Setup
- [x] T001 检查共用录卡入口、账号搜索和成本规则，记录于 research.md
- [x] T002 完成 spec.md、plan.md、data-model.md、contracts/auto-cost.md、quickstart.md

## Phase 2: US1 两端自动触发
- [x] T003 [US1] 在 apps/api/tests/test_auto_purchase_cost_postgres.py 编写排队、回滚和唯一匹配测试，先确认失败
- [x] T004 [US1] 在 db/migrations/0082_auto_purchase_cost.sql 和 apps/api/src/card_platform/repository.py 实现每卡唯一事务队列
- [x] T005 [US1] 在 apps/api/src/card_platform/auto_purchase_cost.py 和 purchase_cost_service.py 实现后台查询、核验及worker接入

## Phase 3: US2 安全边界
- [x] T006 [US2] 在 apps/api/tests/test_auto_purchase_cost_postgres.py 覆盖已有交易、多候选、无结果、账户需验证、有限重试与隔离
- [x] T007 [US2] 在 apps/api/src/card_platform/purchase_cost_api.py 暴露所有者可读的任务状态

## Phase 4: Verification
- [x] T008 回归相关成本/录卡/账号测试，并在 specs/020-auto-purchase-cost/verification.md 记录结果与限制

## Dependencies and Execution
T001→T002→T003→T004→T005→T006→T007→T008；顺序执行。US1先形成最小自动流程，US2验证保护措施。文档核对与测试准备可独立完成，不使用并行agent。
