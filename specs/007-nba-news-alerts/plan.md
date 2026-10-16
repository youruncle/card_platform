# NBA 新闻提示实施计划
Spec: spec.md

使用现有 PostgreSQL / FastAPI / Next.js。新增独立 player_news_alert 表，公开球员情报不存持仓信息。列表 SELECT 通过 player_id 关联未过期记录并经模型输出；读取时过期不依赖清理任务。客户端定时器在最近到期时间重新过滤。

新增 card_platform/news_alerts.py：严格输入验证、近 72 小时校验、幂等插入、只输出球员及历史事件的研究上下文。scripts/nba_news_alerts.py 提供 roster、import 命令供 Codex 自动化使用。导入使用事务和 ON CONFLICT DO NOTHING，服务端生成 72 小时有效期。

新增独立 NewsRiskAlerts UI 组件，列表只增列和 API 类型。按钮可展开新闻事实、因果推断与限制、置信度、到期信息；新闻链接仅 HTTPS/HTTP。无需改估值算法。

测试：先写验证/过期/幂等数据库测试与组件展示测试，确认失败，再实现。使用事务回滚的真实 PostgreSQL 检查，前端组件测试及 TypeScript 校验。执行迁移并刷新本地服务后检查状态。创建每天 10:00 的本任务 heartbeat，保存研究操作指令；检索不变时静默，写入/失败时通知。
