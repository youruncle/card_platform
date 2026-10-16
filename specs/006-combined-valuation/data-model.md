# 数据模型

不迁移数据库。PortfolioItem 新增 valuation_synthesis JSON：算法版本、状态、原因、原估值和 Codex 来源 ID/版本、参与的证据 URL、权重、结果。原始 Codex 展示字段不变。

状态：combined、codex_supported、review_required、insufficient_evidence、original_only。未采用 Codex 时保持当前估值状态和价格；采用时列表状态为 succeeded，原作业状态保存在审计信息。
