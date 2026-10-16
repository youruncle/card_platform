# 每日研究运行说明

工作目录：/Users/song/Documents/Codex/2026-07-19/w/card_platform
每天 10:00（Asia/Makassar / UTC+8）由当前 Codex 任务的 heartbeat `nba` 唤醒。需要本机 Codex 及数据库可用；异常时报告实际失败。调度并不在 API 进程内。

## 研究及导入
1. 运行 `PYTHONPATH=apps/api/src .venv/bin/python scripts/nba_news_alerts.py roster`，取得公开球员 ID/姓名与历史事件。历史保留用于避免旧事件重新标注；不要外发账户/持仓资料。
2. 搜索最近 72 小时公开 NBA 新闻，实际打开来源核验日期和内容。优先官方、球队与可靠媒体。按名单检查伤病/复出、禁赛、角色/交易变化、奖项、里程碑等；只有能说明潜在卡价影响且今天仍相关的事件才写入。
3. 归因明确区分新闻事实和推断，写清“事件 → 出场/表现/关注变化 → 收藏需求或流动性可能变化”，以及适用限制、置信度；没有市场成交支持时不能断言价格已经变化。传闻无法验证则跳过。
4. 为同一现实事件复用历史 event_key，转载或重发不能算新事件。新事件键建议 `injury-球员英文名-事件日期` 等稳定标识；真正复出、确诊变化等实质性进展可用新键。
5. 创建 `work/news-alerts/` 并保存时间命名的 JSON 数组。格式如下（占位内容不得直接导入）：

```json
[{
  "player_id": "球员 UUID，来自 roster",
  "event_key": "稳定现实事件标识",
  "direction": "negative",
  "headline": "新闻标题",
  "facts": "已核验的事实及事件日期",
  "attribution": "中文因果链、当日仍相关原因、限制和不确定性；卡价影响为推断",
  "confidence": "medium",
  "source_url": "https://原始新闻链接",
  "published_at": "2026-09-28T02:00:00+00:00"
}]
```

`direction` 只能 positive / negative，confidence 只能 low / medium / high。发布时间需要真实、带时区，必须在过去 72 小时内。不输入 created_at / expires_at：服务器负责首次标注与 72 小时有效期。

6. `PYTHONPATH=apps/api/src .venv/bin/python scripts/nba_news_alerts.py import work/news-alerts/<文件>.json`。整批事务导入，输出 inserted 与 duplicates。同球员重复 event_key 不更新记录；同一来源有实质性新进展时允许使用新事件键，不会续期。再次 roster 核验结果。
7. 没有新增有用信息保持安静。新增提示、检索/写入失败、需要处理时才通知；记录覆盖限制，不将部分覆盖宣称全量完成。

## 过期与历史
数据库按 `expires_at > statement_timestamp()` 返回有效提示。浏览器按到期时间移除并在回到页面时复核；无需定时 DELETE。历史事实、归因、链接继续保存，不直接修改估值。

## 部署与验证
先由表所有者应用 db/migrations/0075_player_news_alerts.sql，再授予 API 角色 SELECT/INSERT 权限（本机角色 card_platform；本机表所有者 song）。之后重启 API、构建并重启 Web。生产迁移遵循现有版本化流程。
测试：`pytest apps/api/tests/test_news_alerts.py apps/api/tests/test_news_alerts_postgres.py`；数据库测试需要 TEST_DATABASE_URL，所有测试数据事务回滚。前端 `node --import tsx --test tests/news-risk-alerts.test.cjs` 和 `npm run typecheck`。
