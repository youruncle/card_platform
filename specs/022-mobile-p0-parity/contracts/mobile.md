# UI/REST Contracts
现有 `/v1/purchase-cost/items/{id}/{search-plan,search-result,candidates,confirm,audit}` 与 `/v1/purchase-cost/cardhobby/*`，刷新查询使用 refresh:true。候选只允许后台标记 automatic 的可确认项。
现有 `/v1/portfolio/items/{id}/automated-valuation[/retry]`、`/v1/portfolio/identity-review/items/{id}[/research|/confirm]`、`/v1/portfolio/items/{id}[/transactions|/restore|/permanent]` 与 `/v1/portfolio/trash`。owner action 传当前认证用户 id；服务器仍决定所有权。
PurchaseCostPanel props: api, itemId, onChanged；CardHobbyAccount props: api, verification?, onConnected；fetchPurchaseCost(api,id) 返回查询结果。CardManagement props: api,id,ownerId,baseCurrency,onChanged,onDeleted。LibraryActions props 由主代理实现，不与其他组件共享内部状态。
