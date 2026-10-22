# 手机成本组件实现记录

- 新增 `apps/mobile/src/purchaseCostApi.ts`：独立手机契约；计划、搜索、解析与 Web 相同的 fallback 合并，搜索写入始终 refresh:true，POST 180 秒超时，支持调用方 AbortSignal。
- 新增 `apps/mobile/src/PurchaseCost.tsx`：点击查询/重试，登录后继续原操作、当前账户绑定/移除、官方滑块及短信验证、过期恢复、明确选择 automatic 候选后确认、订单审计及快照、人工零价格与币种修改。初始仅读取候选、审计和已有价格，不自动搜索或发送短信。initialResult 可直接展示批量结果。
- 官方滑块仅接受 `https://cms.cardhobby.com.cn/checkcode/index`；消息必须同时匹配 origin、iframe contentWindow、checkcode 数据。订单来源与图片仅允许 HTTP(S)。
- 查询、确认及人工修改均有同步 ref 防重复和卸载后状态保护。查询关闭时可能已有服务端写入，批量调用方必须如实提示并刷新读取。

验证：先运行新增测试，因尚未创建模块而失败；实现后 `npm --prefix apps/mobile test -- --run tests/purchase-cost.test.tsx` 8/8 通过，覆盖超时/refresh、失败后重试、登录后继续、明确选择候选、fallback 合并、零价格、现有价格币种预填、安全链接及 SMS origin/source 校验。`tsc --noEmit` 当次仅剩其他代理正在实现的 CardManagement.tsx TS7053；本模块未出现类型错误。

限制：真实卡淘账号、真实短信/滑块、外部订单及 iPhone 真机未验证。未部署。批量实现由主代理负责；helper 与 UI 文件名使用 purchaseCostApi.ts / PurchaseCost.tsx 避免 macOS 大小写解析冲突。
