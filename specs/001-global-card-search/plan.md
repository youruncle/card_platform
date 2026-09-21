# Implementation Plan: 全库中英文搜索
Date: 2026-09-21 | Spec: [spec.md](spec.md)
## Summary
沿用现有卡库分页接口，增加可选 search JSON 参数。读取当前账号轻量搜索投影，先匹配/筛选再分页，仅对结果页读取完整 PortfolioItem。前端新增独立筛选栏组件，页面刷新和轮询共用当前查询引用及请求版本保护。
## Technical Context
Python 3.12 / FastAPI / psycopg / PostgreSQL 16；TypeScript / Next.js 15 / React 19。
不新增外部搜索服务、数据库扩展或运行时依赖。约 519 张现有卡库；搜索投影在后端处理，不下载全库到浏览器。O(N) 检索适用于当前规模，保留未来索引优化边界。
## Constitution Check
五项通过：架构延续；别名可追溯但不是身份裁决；测试覆盖原症状；鉴权 owner 来自 session；不修改估值/账本。维持用户指定原目录，不开分支、不清理工作区、不提交混合用户改动。
## Project Structure
- apps/api/src/card_platform/portfolio_search.py：约束模型、名称归一化、别名展开、轻微拼写容错、属性匹配、轻量投影查询。
- apps/api/src/card_platform/api.py：现有分页 API 可选 search 参数与校验。
- apps/api/src/card_platform/repository.py：可选 item_ids 约束，仅加载匹配结果页。
- apps/web/lib/portfolio-search.ts：类型、验证、查询串构建。
- apps/web/components/collection-search.tsx：输入、高级筛选、标签与反馈。
- apps/web/app/page.tsx + lib/api.ts + globals.css：布局、全库请求与竞态隔离。
## Steps / Interfaces
1. Backend RED：API 搜索现有第二页卡片、所有者隔离、中文名字、组合属性/非法范围失败；再实现 SearchFilters / matcher / projected rows。
2. Frontend RED：输入查询发往后台而非本页过滤、控件位置、高级条件与清空、页码重置/过期响应保护；再接入控件和刷新。
3. 完整 pytest / 隔离 PG / Ruff；前端测试、typecheck/build；只读真实卡库查询与浏览器检查；独立代码审查、修复重要发现、再验证、部署。
## Review Focus
未知属性的 false/零值误匹配、ID/限编数字模糊、多词姓名和同姓误匹配、中文空格、空白及错误 JSON、金额币种、状态与正式估值有效性、owner 切换/防旧响应、轮询与搜索分页条件丢失。金额查询使用买入记录 fx_rate_to_base；估值查询仅与账户本位币同币种的有效正式值。
## Execution
Superpowers TDD + verification 与 Spec Kit 共用此计划和 tasks.md；按 AGENTS.md 持续推进已批准功能。保留规格与验证记录，不额外创建重复计划。
