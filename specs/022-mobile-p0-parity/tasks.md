# Tasks: 手机 P0 对齐

## Setup and foundation
- [x] T001 建立 spec/plan/contracts 与当前接口核对于 specs/022-mobile-p0-parity/
- [x] T002 先写新增交互失败测试于 apps/mobile/tests/，保持既有测试可用

## US1 成本
- [x] T003 [P] [US1] 实现 apps/mobile/src/purchaseCostApi.ts 和 PurchaseCost.tsx，覆盖绑定/官方短信验证/候选确认/审计/手工价格，先写 tests/purchase-cost.test.tsx
- [x] T004 [US1] 在 apps/mobile/src/LibraryActions.tsx 增加所选批量获取，保留已知成本和部分失败状态

## US2 US3 US4 详情管理
- [x] T005 [P] [US2] 在 apps/mobile/src/CardManagement.tsx 发起估值、轮询和错误反馈，先写 tests/card-management.test.tsx
- [x] T006 [US3] 在 apps/mobile/src/CardManagement.tsx 身份候选/逐字段依据/人工编辑确认
- [x] T007 [US4] 在 apps/mobile/src/CardManagement.tsx 切换持仓、录入交易和历史

## US5 回收站
- [x] T008 [US5] 在 apps/mobile/src/LibraryActions.tsx 实现软删除/批量失败保留/恢复/二次永久删除，先写 tests/library-actions.test.tsx

## Integration and validation
- [x] T009 集成 apps/mobile/src/App.tsx、LibraryPage.tsx 与 styles.css，保留现有缓存和未提交改动
- [x] T010 全量 mobile tests/build、窄屏交互检查与代码复核，记录 specs/022-mobile-p0-parity/verification.md

## Dependencies / execution
T001 → T002；T003 与 T005-T007 可分文件并行；T004 依赖 T003 导出的查询函数；T008 独立；T009依赖组件契约；最后T010。全部在当前工作区，不提交其他人的改动。先完成单卡MVP再集成批量和回收站，所有P0完成后统一验证。
