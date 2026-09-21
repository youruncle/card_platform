# Data Model

SearchFilters: q <= 160 字；player/brand/product/insert/parallel/card_number/grade_company 数组每项 1–80 字，最多 20 项；year_min/max 1800–2200；serial_number/serial_min/max 正整数；grade_min/max 0–10；cost_min/max 与 value_min/max 非负有限数；rookie/autograph/memorabilia/numbered/has_cost/graded 为可空 bool；valuation_status 为数组枚举 valued/unvalued/queued/running/failed/insufficient_evidence。

范围下界不得大于上界。额外未知字段拒绝。单纯数字 q 匹配 display_id 精确值；ID 前缀也精确。其余关键词包含卡号/年份但无数字容错。

SearchProjection: owner-scope id/display_id、印刷身份、赛季、评级/限编、首次买入总成本本位币、最新有效正式估值及作业状态、created_at。只读、请求内使用、不保存搜索历史。

SearchResult: 原 PortfolioPage 契约不变 items/total/page/page_size/total_pages。
