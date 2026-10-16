# Data model
- player_price_sale: source_id唯一，player_id、card_key、identity JSON、成交价/时间/URL、标题、观察时间；明确同款和人民币已售事实。
- player_price_basket: (player_id, month)唯一，selected_on、cards JSON（key、identity、90日笔数）。凑齐三种后按月冻结；不足三种时允许后续采样补齐，仍不生成球员涨跌。
- player_price_snapshot: (player_id, report_date)唯一，result JSON、computed_at，两个窗口同一篮子。
- player_price_state: player_id唯一，requested_at、last_attempt_at、last_success_at、error、coverage JSON。每天到期或用户请求；失败6小时后重试。

- player_price_source_conflict: source_id唯一，永久隔离同一来源的冲突事实，避免公开目录最新行覆盖后重新入价。
- 旧快照: 当报告日未生成新结果，返回最近历史日期、stale状态、last_change_pct；change_pct保持null，不参与当日排序。
