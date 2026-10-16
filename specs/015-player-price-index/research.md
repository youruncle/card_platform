# Research
- Decision: 复用卡淘SearchCommodity已售Status=-2、有出价结束拍卖；现有parse_market_item提供可核验事实。Rationale: 不是挂牌价。Alternative: 成交评价覆盖较少，初版使用结束拍卖并注明。
- Decision: 标题严格匹配明确字段，未知保持候选。Rationale: 搜索范围不证明同款。Alternative: 模型全量识别成本高且误配难审计，初版暂不自动图像确认。
- Decision: 独立索引证据不写market_evidence指针。Rationale: 原证据绑定可能是搜索线索，避免污染现有估值。
- Decision: 后台逐球员更新+持久到期状态。Rationale: 可跨重启恢复、并发锁、失败退避；不依赖桌面自动化。
