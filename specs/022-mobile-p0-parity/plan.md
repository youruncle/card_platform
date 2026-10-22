# Implementation Plan: 手机 P0 对齐

## Technical Context
React 19 / TypeScript / Capacitor 8；现有 MobileApi bearer transport，FastAPI，PostgreSQL。Vitest/Testing Library 与 Vite production build。

## Constitution Check
保留当前未提交工作，在当前 codex 工作分支增量开发；不创建会丢失现有依赖的空白 worktree。复用现有鉴权、身份审核、成本核验、估值和账本接口；不改数据库、真实卡库或模型配置。所有未知保持未知。无新依赖。

## Architecture
- PurchaseCost.tsx + purchaseCostApi.ts：单卡成本、账号绑定/验证、候选和审计；库中批量逐卡复用。
- CardManagement.tsx：估值任务、身份编辑与候选确认、交易及历史，作为详情扩展。
- LibraryActions.tsx：所选卡片批量成本/软删除，TrashPage 回收站。
- LibraryPage.tsx / App.tsx：保留缓存、页面状态和导航，增加入口，将 user.id/base_currency 向下传递。
- 扩展 MobileApi 可选超时以适配卡淘长查询；保持原默认和请求隔离。

## Execution
按 tasks.md 逐项执行，先行为测试失败再实现。成本和详情作为独立组件可交由子代理实现，主代理集成与复核；禁止子代理再次委派。保留 dirty worktree，不自动提交/推送/部署。

## Validation
组件测试覆盖真实交互与错误恢复；全套 mobile tests 和 production build；模拟 HTTP 的窄屏浏览器流程验证，外部卡淘与原生设备验证单独报告。
