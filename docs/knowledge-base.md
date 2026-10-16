# 可迭代卡牌知识库

## 架构

知识库分为工作区草稿和不可变发布快照。实体、别名、关系、未知词观察在工作区内编辑；
只有 `approved` 内容会进入 `knowledge_version.snapshot`。运行时 `KnowledgeResolver` 只消费一个
版本快照，因此一次解析、搜索计划或成交分类可以记录明确的 `knowledge_version`。

实体类型包括 manufacturer、brand、license、product、insert、parallel、attribute、
player 和 card。具体卡片通过
`attributes` 渐进保存赛季、厂牌、产品、card number、insert、parallel、serial total、
RC/AUTO/patch，以及正反面参考图与可解释视觉特征；无需频繁改表。

## 安全发布

- 编辑、审核、发布权限由 `knowledge_member` 分离。
- 新迁移不会自动授予任何人权限。成员为空时，现有 `app_user` 必须在管理页显式执行一次
  “认领管理员”；数据库 advisory lock 保证只能有一位首任管理员。
- 发布要求所有活动内容已审核，并同时检查前端提交的 workspace revision。
- 发布和回滚使用 PostgreSQL transaction advisory lock，版本快照不可变。
- 写 API 接受 `Idempotency-Key`，所有变更记录在 `knowledge_audit_log`。
- 模型/规则只能写入 `knowledge_observation`，不能直接进入发布快照。

## 迁移策略

`POST /v1/knowledge/seed` 将现有 Python insert、产品、parallel 和属性别名幂等导入工作区，
来源标为 `python_builtin_migration`。迁移期旧常量仍保留作为数据库不可用时的兼容回退，
但 insert 规范化已统一通过 `KnowledgeResolver`。后续可在各 worker 启动时加载当前发布快照，
逐步移除兼容常量。

## 发布影响与重估

发布或回滚会写入 `knowledge_impact`，列出命中变更知识的 `market_comp` 和
`collection_item`。`POST /v1/knowledge/versions/{id}/revalue` 对受影响持仓幂等创建
`valuation_job`。估值 worker 应把版本号写入估值 evidence；历史成交的分类保留原始记录，
按当前版本重新派生，而不覆盖证据。

## 管理入口

现有网站侧栏“知识库管理”，路径 `/knowledge`。首版支持实体与别名查询/创建/审核、
内置数据初始化、版本发布和回滚。关系、未知词观察、版本影响与批量重估 API 已提供，
后续可在同一页面增加专用表格和历史回放可视化。

## v3+ 批次与覆盖矩阵

`0016_knowledge_graph_batches.sql` 增加来源、逐对象证据、赛季/产品适用性、批次和覆盖矩阵。
可重复运行的 `basketball_mvp_v3.json` 清单由 `knowledge_manifest.validate_manifest` 做结构、
重复、关系端点、来源与固定案例回放校验；API 的 dry-run 还会与数据库当前基线做同类型
别名冲突检测。只有零冲突且 First Steps、Dreamland、Night Owls、National Treasures、
One and One、RPA、Donruss、Donruss Optic 全部回放成功的批次才可应用。

覆盖状态含义：

- `covered`：已登记可追溯 checklist，覆盖到该矩阵行声明的粒度。
- `partial`：来源存在，但 insert/parallel/具体卡片尚未完全展开。
- `pending_source`：仅有规划骨架，不进入正式运行快照，不得宣称已覆盖。
- `needs_review`：候选已有来源或模型草案，仍待人工核验。
- `not_applicable`：经证据确认该组合不适用。

当前 MVP 仅把有来源支持的现代核心实体发布为正式知识；Select、Mosaic、Contenders、
National Treasures 等未完成逐赛季 checklist 的产品明确保留为 `pending_source`。Parallel
必须通过 `knowledge_applicability.product_entity_id + season` 表达产品上下文；后续批次不应
把同名颜色跨产品直接当作同一具体 parallel 身份。

## 分批建设顺序

1. MVP-A：2023–24 Donruss、Donruss Optic、Prizm 与 2025–26 Topps Chrome 骨架及核心属性。
2. MVP-B：Select、Mosaic、Contenders、National Treasures、Immaculate、Flawless、
   One and One 的逐赛季 checklist、insert 和产品专属 parallel。
3. 历史批次：Upper Deck、Fleer、SkyBox、Leaf、Bowman，按 manufacturer、brand、
   license 和年代分段录入。
4. 卡片级：checklist card identity、正反面参考图、图片来源、可解释视觉特征与图像校验。

## NBA 球员别名目录（2026-09）

`nba-player-aliases-v1` 是知识库管理可导入的独立批次，覆盖 nba_api 历史球员目录与
新浪近期阵容中的新增球员。可发布清单位于 API 包内
`card_platform/data/nba_player_aliases_v1.json`，因此部署后的离线兼容路径使用同一份数据。
原始事实摘录固定在 `data/knowledge_sources/nba_aliases_20260923.json.gz`，补充核验在
`nba_aliases_reviewed.json`；`scripts/build_nba_player_aliases.py` 可离线重建。
构建需要 `opencc-python-reimplemented` 做繁简转换，运行服务不依赖该库。

身份别名只保留无跨球员冲突的完整姓名/已审核名称。姓氏、重名，以及会被另一球员中文
全名包含的形式（例如父亲姓名包含在“二世”姓名中），只写入 `attributes.query_aliases`。
这些词用于扩大候选搜索，不进入 `KnowledgeResolver` 身份索引。大陆阵容译名作为
`preferred_query_aliases` 优先使用；明确卡片别名和人工知识优先于新目录的通用变体。

覆盖分母、缺失中文来源及冲突清单记录在 `data/knowledge_sources/nba_aliases_coverage.json`。
“目录全量扫描”不表示所有历史球员已有中文译名，也不表示昵称已穷尽；未取得可靠来源的
条目保留 `missing_source`。active_at_source 是来源快照字段，不作实时合同/阵容断言。
实体的 `alias_evidence` 保存中文资料的 Wikidata、中文维基条目、Sina 阵容及补充核验链接；
实体主来源 nba_api 只代表英文目录，不能据此声称中文译名来自 NBA 官方。

更新时替换/增加来源摘录并重建，验证同名、父子、多球员卡及真实中文订单，然后通过原有
批次预检、审核发布路径生效；不直接改写历史快照或自动覆盖已有购入交易。


## 官方篮球清单自动录入（2026-10）

入口 `/knowledge` 显示工程进度、已发现候选的发布比例、逐产品来源和解析异常。
自动同步执行 `.venv/bin/python scripts/sync_official_checklists.py --apply`；
只读持仓验证执行 `.venv/bin/python scripts/replay_official_knowledge.py`。
本次范围为2025-10-04至2026-10-04，仅篮球；发行日期未知独立计数，不能以赛季代替。
数据账本位于 `data/official-checklists/status.json`，目录与原始证据保存在同目录。

官方事实自动批准，先通过结构化检查，再按来源原子替换和发布，无人工审核步骤。
无法可靠解析的行保留原文并自动隔离，不作为卡片身份事实。独立发布保留其他来源的草稿和已发布事实。
查询和估价按产品、年份、球员、卡号组合读取已发布知识；唯一一致的缺失特卡名称可自动补全并附官方页码。
发行清单不等于成交证据，不直接产生市场价格，也不据此猜测实物平行、限编或签字。

完整记录见 `specs/009-official-checklist-knowledge/verification.md`。当前任务尚未全量完成：Panini来源访问受限，
部分产品发行日期未知，部分PDF行仍待自动恢复。工程完成率与目录覆盖率分开显示，不把受阻任务记为完成。
