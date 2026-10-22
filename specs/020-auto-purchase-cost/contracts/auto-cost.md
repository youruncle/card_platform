# Contract

GET /v1/purchase-cost/items/{item_id}/automatic：需登录且拥有未删除卡片；返回任务status/attempts/result/时间，无任务返回null；其他账户或已删除卡404。成本写入仍通过既有服务，不新增客户端POST触发要求。
