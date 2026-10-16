# API
登录鉴权。GET /v1/player-prices?date=YYYY-MM-DD&direction=gainers|losers：返回report_date、timezone、window_days、min_sales、players数组；每行player_id/name、status、change_pct nullable、cards、coverage、refresh状态。
POST /v1/player-prices/refresh：仅队列当前用户活动篮球卡库球员，202返回queued数量。GET不采集或改写快照。
后台每日默认07:00后统计前日。来源异常保留旧快照，响应用refresh.error标注。空卡库返回空数组。

若报告日没有新快照，展示最近已保存的历史快照，球员行report_date注明真实日期，status=stale，change_pct=null，last_change_pct保留历史值；不混入当日排名。
