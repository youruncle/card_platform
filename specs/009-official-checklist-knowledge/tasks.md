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

## 按年份建设图文与向量知识库（2026-10-21 修订）

本节是用户修订后的任务队列；用户已授权执行近三个赛季首批。来源、年度产品目录为建设起点，持仓只用于附加验证。详细范围以 spec.md 的 FR10–FR21 及 plan.md 的同日修订为准；实际首批交付与未完成项见 three-season-verification.md，不以少量设计示例勾选年度完整覆盖。

- [x] T018 [FR10–FR11, FR19] 合并现有发布知识与官方年度目录，建立厂商/年份/产品母表，分别登记文字、设计图、逐卡正反面、向量、来源和阻塞状态；从 2025–26 正式产品开始安排年度队列，复用已有事实。
- [ ] T019 [FR13–FR15] 试运行 Topps、Panini 和历史补充站点的文字、原图及正反面资料获取；留原始证据，验证分页、图片可用性、使用范围、限速与失败恢复，形成来源可行性报告。
- [ ] T020 [FR12–FR14, FR21] 定义图文媒体/实体关系、配对依据、访问边界、来源/内容哈希和版本合同；明确具体迁移、查询接口、文件与验证任务后，按最小改动接入现有知识库。
- [ ] T021 [FR12–FR15, FR19] 完成 2025–26 Topps Chrome Updates Basketball 图文样板：基础/特卡/平行/签字物料文字及可用设计图、逐卡正反面，缺图逐项登记；验证真实知识查询返回图片与来源。
- [ ] T022 [FR16–FR18] 对比图像/图文编码候选，确定文本与图像检索空间、预处理及模型版本；验证 pgvector 部署兼容性和资源，明确编码、存取、重建/切换接口与迁移，完成样板图文索引。
- [ ] T023 [FR17–FR18, FR20] 实现结构化/关键词/向量融合召回和证据重排；与关键词及现有 SIFT/几何候选基线比较，覆盖错产品/赛季、相邻平行、未确认字段和图库外卡，测 Recall@K、版本混淆、延迟及资源。
- [ ] T024 [FR10–FR15, FR19] 依照已发现年度目录逐产品扩充 2025–26 Topps 和 Panini，文字、图片、向量同步入账；所有产品有处置状态，受阻项明确保留，不以热门或持仓代替年度范围。
- [ ] T025 [FR10–FR15, FR19] 按同一流程逐产品建设 2024–25、2023–24 并继续向历史推进；具体产品清单来自 T018 母表，每个年度形成独立批次与缺口报告。
- [ ] T026 [FR10–FR15, FR19] 逐年补 1996–97 起历史官方档案及可靠补充来源，将 Upper Deck、Fleer、SkyBox 等按当时发行身份归档；保留档案不可获取、年份未知、图片缺失和第三方证据分级。
- [ ] T027 [FR18–FR21] 每批验证幂等、来源/媒体有效性、账户权限、事实与索引版本一致性及回滚；顺序发布，运行按目录取样的独立回放和必要业务回放，记录实际测试及缺失样本。
- [ ] T028 [FR11, FR19–FR21] 展示按年份/厂商/产品的文字、设计图、逐卡正反面/配对、向量覆盖、检索效果和失败原因，区分工程进度与内容覆盖；正式新产品资料走增量队列，不因未知母集显示全部完成。

Dependencies: T018→T019→T020→T021→T022→T023→T024→T025→T026；T027 随每个样板/产品/年度批次执行；T028 从 T018 状态口径开始并随各批验收补齐。历史来源受阻不阻止其他已核实产品推进，不能因此把该年度标为完整。

近三年首批执行记录：已发布 v19，126 产品、701,473 条清单版本事实（169 新增，其余复核）、486 设计图/43 产品、972 图文向量。116 项相关检查、线上文件/查询校验和重复发布通过，30,095 条非本批修改的已发布实体保持原值。83 产品仍缺参考图，实卡正反面配对为 0；应用内向量入口、全清单文本编码和独立检索评测未完成，因此 T019–T028 的完整要求继续逐项推进，不能凭本批勾选全部。详细来源、年度缺口、性能和测试范围见 three-season-verification.md。


## 2026-10-22 目录、详情与图库改造

- [x] T029 固化已批准浏览范围、合同和共享类型，specs/009-official-checklist-knowledge/contracts/browser.md、packages/card-core/src/knowledge-browser.ts。
- [x] T030 [US4] 先写失败行为测试，验证已发布实体＋附件目录、厂商/赛季、别名、分页与真实缺口，apps/api/tests/test_knowledge_browser.py。
- [x] T031 [US4] 构建按发布版本缓存的统一目录/卡牌查询与受控媒体，apps/api/src/card_platform/knowledge_browser.py、knowledge_browser_api.py、knowledge_api.py。
- [x] T032 [P] [US4] 网页目录/总览/知识说明和管理入口分离，apps/web/app/knowledge/、apps/web/components/knowledge-browser.tsx、apps/web/tests/knowledge-browser.test.cjs。
- [x] T033 [P] [US5] 手机目录/系列/卡牌详情、筛选搜索和分页，apps/mobile/src/KnowledgePage.tsx、apps/mobile/tests/knowledge.test.tsx。
- [x] T034 [US6] 两端图片放大、侧别切换/缩放/旋转/对照、鉴权与空态；API媒体路径/哈希/权限回归，apps/api/tests/test_knowledge_browser_api.py、两端浏览组件。
- [x] T035 完成相关回归、构建与真实浏览器/本机发布资料只读回放，记录 specs/009-official-checklist-knowledge/browser-verification.md 并按FR22–FR27收敛。

Dependencies: T029→T030→T031；T029后T032/T033可按稳定合同独立执行；T031/T032/T033→T034→T035。T029–T035属于应用内浏览交付，不据此勾选原年度全量采集/向量评测任务。

### 2026-10-22 云端采集增量记录（不替代年度完整性验收）

- [x] 专业资料站真实文字捕获与逐字段支持锚点：49条产品事实/9产品/10来源页，第三方来源单独归属。
- [x] PSA实卡31个样本/30原缺图产品/62面，逐面文件与认证来源审核；28清单关联、3仅产品绑定并保留冲突。
- [x] 两张原厂整页设计资料，包装/卡堆/失败响应隔离；累计550图、73有图产品，53仍缺图并保留处置。
- [x] 同模型1100图文向量与四方向离线查询smoke；应用检索与独立评测仍按原未完成项处理。
- [x] 仅云端发布v20及单产品范围修正v21，实际回读图片/来源/清单/向量一致；保留旧版本、其他事实及worker=true；本地运行知识库未更新。
