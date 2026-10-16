# Tasks: 官方清单自动知识

工程进度=sum(已完成任务权重)，只在任务交付物和验证均完成时打勾；产品覆盖率独立计算。实施方式：本任务内持续执行，保留用户未提交文件。无需人工审核/再次批准。

- [x] T001 范围、规格、数据模型与来源研究框架，specs/009-official-checklist-knowledge/ (5%)
- [x] T002 [US1] 官方入口发现、日期依据和冻结目录，data/official-checklists/catalog.json (10%)
- [x] T003 [US1] 下载、PDF/XLSX/CSV抽取、自动检查与可恢复缓存，apps/api/src/card_platform/official_checklists.py (20%)
- [x] T004 [US1] 自动幂等入库、发布与来源证据，scripts/sync_official_checklists.py (15%)
- [x] T005 [US2] 产品/年份/卡号/球员作用域查询与唯一字段补全，apps/api/src/card_platform/knowledge_card_catalog.py (15%)
- [x] T006 [US2] 接入运行时搜索和估值、快照证据，apps/api/src/card_platform/knowledge.py (10%)
- [x] T007 [US3] 可见百分比与来源统计，apps/web/app/knowledge/page.tsx (10%)
- [x] T008 [US3] 执行真实入库、现有持仓回放、回归与总结，specs/009-official-checklist-knowledge/verification.md (15%)

Dependencies: T001→T002→T003→T004→T008；T001→T005→T006→T008；T003→T007→T008。
Parallel: 官方来源研究与本地知识索引可独立推进；生产数据库发布顺序执行。

## 20年扩展（2026-10-04）
- [x] T009 复用447项目录，扩展日期窗口，保留历史缺口和未知日期；成功率与处理率分离。
- [x] T010 可恢复限速下载，跨worktree数据库互斥，分批入库后单次发布。
- [x] T011 补齐可访问Topps历史目录，无法获得官方原文的档案明确列缺口。
- [x] T012 全量发布验证、真实持仓只读回放及负样本回归，更新知识页面进度。

Ruling: 在独立worktree选择性复用原目录任务文件；原目录仅只读，运行时状态稍后通过窄范围部署更新。发行日期缺失不以赛季替代。

Completion evidence: history-verification.md. T002 freezes the known directory and records archive/date gaps; it does not assert exhaustive 20-year coverage.

## 30年三厂商扩展
- [x] T013 核实UD官方目录与品牌归属；冻结分厂商/年份清单，补查Topps/Panini历史缺口。
- [x] T014 测试并实现UD可审计解析及跨品牌运行时查询，隔离非篮球和冲突。
- [x] T015 扩展日期窗口，恢复下载、解析、入库和发布，验证幂等及跨目录锁。
- [x] T016 真实持仓回放、负样本及相关测试，部署相关改动，报告三家进度和缺口。

Ruling: 沿用用户已批准的自动执行范围及现有架构，无重复审批。原目录仅选择性读取；部署仅同步本任务文件。基线为v11而非交接中的v10。

30年交付证据：30-year-verification.md。T013冻结已知目录并记录无法访问的历史档案；未声称全量穷尽。工程100%与30年覆盖率未知独立显示。

## UD失败来源修复（2026-10-05）
- [x] T017 修复截断缓存、错误官方入口和重复候选，按真实证据恢复混合清单篮球行，保留非篮球排除；完成回归、发布、幂等和部署。
记录：.specify/bugs/ud-source-recovery/assessment.md。已授权持续修复，不改持仓、身份或价格。

T017验收：.specify/bugs/ud-source-recovery/verification.md；13来源恢复发布、1排除、3项外部获取/证据缺口仍保留，不声称失败全部清零。
