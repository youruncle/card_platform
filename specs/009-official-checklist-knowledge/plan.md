# 自动官方清单知识实施计划

Goal: 自动采集近20年官方篮球清单并发布可查询知识，回放持仓并暴露真实进度。
Spec: spec.md
Tech: FastAPI/Python/PostgreSQL/Next.js，复用现有knowledge表、不可变快照与授权。

## Constitution Check
保留工作区修改与现有接口；官方源按用户本次指令自动批准，仅机器检查结构与冲突；未知字段保持未知。仅追加知识，不写历史卡片和估值。修改范围在新导入模块、查询模块、现有运行时的窄接口。

## Architecture
1. 官方目录 discovery → 冻结source manifest（发行日期证据/未知状态）→ 原文件content-addressed缓存。
2. PDF/XLSX抽取结构行；每行存section、卡号、球员、team、来源页号/行号，卡种作用域明确；无法解析的片段保留并计数。
3. 生成现有knowledge manifest，自动approved，幂等apply并以现有snapshot发布，标明auto_official来源。
4. ScopedCardCatalog索引先按产品/年份/卡号筛选再核对球员，仅唯一一致字段补全；共同字段可回填，版本冲突保留。并入KnowledgeResolver.normalize_identity，估值入口及知识validate统一使用。
5. JSON运行账本原子写；API只读展示。发现未完成时coverage为null；失败计已处理不计成功。
6. 全部有效持仓只读回放，记录原/新身份、知识来源、知识查询与卡淘query plan；586及负样本必测。不自动发布未经视觉确认的新价格。

## Files
- New apps/api/src/card_platform/official_checklists.py: discovery、抽取、manifest、进度统计。
- New apps/api/src/card_platform/knowledge_card_catalog.py: scoped index与补全。
- New scripts/sync_official_checklists.py: 缓存、恢复、自动入库/发布、进度。
- New scripts/replay_official_knowledge.py: 持仓只读回放。
- Modify knowledge.py / knowledge_repository.py / valuation_worker.py / knowledge_api.py: runtime消费及progress endpoint。
- Modify apps/web/app/knowledge/page.tsx: 当前批次与进度。
- Tests: test_official_checklists.py / test_knowledge_card_catalog.py / postgres integration。

## Review Focus
跨产品同卡号、跨年份同产品、同卡号多平行、不同球员、目录抓取失败与空目录、分页/跨页标题、日期未知、重复运行、非官方重定向、未解析行、服务未刷新旧版本。

## Validation
先失败测试后实现；来源解析/幂等/进度/知识查询单测，既有卡淘及知识回归，真实官方下载、PostgreSQL发布与运行时查询、618张持仓回放。所有进度通过tasks与运行账本复算。

## 20-year execution amendment
See history-verification.md for the immutable official_artifacts design, bounded scoped cache, shared PostgreSQL publication lock, one-shot publication and source gaps. Attachments remain part of existing knowledge versions and their checksums.

## 30年扩展实施
以v11的500个已发布产品为基线。新增Upper Deck官方目录发现及HTML结构表解析，保存原始表格与行证据；复用附件发布和只读回放。Topps/Panini将窗口扩为1996-10-04至2026-10-04，复用缓存，补查历史官方档案。
