# API
登录鉴权。GET /v1/player-prices?date=YYYY-MM-DD&direction=gainers|losers：返回report_date、timezone、window_days、min_sales、players数组；每行player_id/name、status、change_pct nullable、cards、coverage、refresh状态。
POST /v1/player-prices/refresh：仅队列当前用户活动篮球卡库球员，202返回queued数量。GET不采集或改写快照。
后台每日默认07:00后统计前日。来源异常保留旧快照，响应用refresh.error标注。空卡库返回空数组。

若报告日没有新快照，展示最近已保存的历史快照，球员行report_date注明真实日期，status=stale，change_pct=null，last_change_pct保留历史值；不混入当日排名。


## v2 兼容扩展（2026-10-17）
- POST /v1/player-prices/refresh?player_id=<UUID>：可选参数，限定当前账户持仓篮球球员；不在范围内404，非法UUID422。省略仍排队全量。
- GET 新增 summary：tracked、with_sales、with_trends、with_index、queued、source_errors。
- 每球员新增 market：report_date、sale_count（近90日独立有效成交）、card_count、trend_count（卡款数）、latest_sale_at、cards（最多20）、cards_truncated、window_days=30、min_sales=3、current_window、previous_window。
- market.cards沿用PriceCard结构；current/previous价格及数量属于不重叠30日窗；sources至多30笔近90日确认成交。顶层cards/change_pct仍属于旧三卡7日指数，不能混用。
- coverage新增rejection_reasons，每轮以独立商品统计；conflicting_source/invalid_observation/multi_card/grade_unclear/season_unclear/identity_unresolved。旧coverage无此字段时不伪造原因。board优先显示最近尝试coverage，旧指数快照仍单独标记日期。
- 部分采集失败可保存确认成交，使market可见；不更新指数快照或last_success_at，refresh.error保持失败说明。
