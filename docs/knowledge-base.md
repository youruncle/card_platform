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
