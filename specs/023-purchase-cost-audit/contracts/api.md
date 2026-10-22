# 兼容性

沿用 GET `/v1/purchase-cost/items/{id}/candidates` 与现有 owner 校验。

响应保留 candidates/rejected/reason，增加 diagnostics 汇总。rejected 增加 identity_missing、automatic_identity、order_errors、allocation_blocked，仅供解释；缺运费而未生成 unit 的原始订单行仅进入 rejected，绝不能作为 confirm 的 unit_id。

reason 区分身份不足、金额/整单运费受阻、已占用、同步范围无候选。原自动确认、已有人工作账/零成本、防重复、fresh order scope 与事务边界保持原义。
