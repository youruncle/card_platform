# 调研

列表目前分别取 latest_item_valuation 与最新成功 codex_multimodal 快照，汇总调用同一 list_items。故在 list_items 的盈亏计算之前派生综合估值，无需写入估值历史或改变 worker。

本地 751 条成功 Codex 快照均未绑定 identity_version_id，近期样本明确 reliable_price=false。仅凭来源标签或置信度不能解除证据门槛。使用显式 verified=true 的成交/可比证据合同；历史文本和混合 public_research 链接保留但不升级。

考虑过直接平均（忽略证据，拒绝）和写回 valuation_snapshot（会让综合值再次参与合并并混淆来源，拒绝）。选择无副作用、确定性的派生值与离线审计。
