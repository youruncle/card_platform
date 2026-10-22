# 手机 P0 集成代码审查

审查及复审日期：2026-10-17。仅审查本轮 P0 组件、接入点及其既有后端契约；未将其他 dirty 修改归入本轮，未修改业务代码。

## 复审结论

上轮剩余 P1、P2 均已关闭。本次窄范围复审没有发现新的重要残留问题。

### 已关闭 [P1] 批量成本刷新覆盖缓存后新增的人工成本

`purchaseCostApi.ts` 的 `fetchPurchaseCost()` 增加第 4 个 `options` 参数；共享 `/search-result` 请求使用 `options.refresh ?? true`，首次及 fallback 合并结果沿用同一模式。单卡仍默认主动刷新，`LibraryActions.tsx` 的批量调用明确传入 `{refresh:false}`。

已核对真实后端：`resolve_search()` 使用同一 refresh 参数，在事务内重新执行 search plan；`confirm()` 在锁定卡片及交易后，对 `refresh=False` 保留已有交易和成本来源。因此保留规则由后台写入边界落实，不依赖手机旧缓存。测试同时覆盖批量调用参数、helper 的 `refresh:false` 请求，以及原有单卡与 fallback 的 `refresh:true` 行为。

### 已关闭 [P2] 失效选择被静默跳过或导致隐藏卡片删除

批量成本和删除均在开始前调用 `selectionIsCurrent()`。存在不在当前 `items` 的已选 ID 时，本次操作立即退出、不发出请求，将选择修正为当前列表范围并显示“选择已变化……请核对选择后重新操作”。这消除了原先成本静默过滤后报告完成、删除却继续操作隐藏 ID 的范围差异。删除回归明确覆盖不发请求及修正选择。两条业务路径共享相同检查；已开始的批次仍保持开始时的固定范围。

### 其余已反馈修复

- **原生长超时**：`MobileApi` 使用 `TimedRequestInit.requestTimeoutMs` 传递显式超时；`nativeRequestOptions()` 将其应用于原生 connect/read timeout，JSON 卡淘请求为 180 秒，普通请求保留 30 秒，multipart 保留 180 秒。浏览器分支剔除内部 metadata，未放入 HTTP headers。
- **真实估值运行状态**：`identifying/searching/calculating` 已纳入运行态判断和中文显示，继续轮询且禁止再次发起；对应三个阶段均有回归。
- **交易提交成功、后续读取失败**：POST 成功后立即消费表单并通知变更，历史读取失败单独提示“已保存，历史刷新失败请刷新”，不再保留同一交易供直接重复提交；相关回归通过。
- **身份确认成功、后续读取失败**：成功状态与刷新失败已分开，确认理由在成功后清空，已有对应回归。
- 批量删除部分成功后使用最新选择去除成功项；失败项及暂停未处理项保留选择，未发现 `onChanged` 的可确认错误。

按主代理确认的产品行为，单卡主动查询切换主导航后允许当前授权操作完成；未将此行为当作缺陷。批量离开卡库后暂停后续卡片，界面说明当前请求可能已保存。

## 本次验证

在 `apps/mobile` 执行：

```text
npm test -- tests/library-actions.test.tsx tests/request-lifecycle.test.ts tests/library-page.test.tsx tests/card-management.test.tsx tests/purchase-cost.test.tsx tests/capture-shared.test.ts
npm test -- tests/native-transport.test.ts
```

两次合计 7 个测试文件、53 个测试，全部通过。其中管理组件 15 项、成本组件及 helper 9 项、批量/回收站 7 项、原生 transport 2 项。

未连接真实卡淘、真实 iPhone、生产数据库，未执行真实资金写入；本次复审未运行完整 mobile 测试或生产构建。并发成本保护结论依据已核对的请求参数与后端事务/锁定路径，未使用真实账户数据回放。生产构建及全量验证由主任务统一记录。
