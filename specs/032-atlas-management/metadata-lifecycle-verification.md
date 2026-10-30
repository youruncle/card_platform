# 有效身份元数据与发布/回滚时间线收尾验证

2026-10-30，继续同一隔离分支 `codex/atlas-management-20261030`，基线 `b5a1a4c5`。本次仅收尾首版纠错审核的两项残余，无生产schema变更，无部署/push/merge，未改原始来源、实卡、收藏、账本或价格分组。

## 有效身份与原始来源分开

依赖核对在 `metadata-lifecycle-followup.md`：球员/年份/卡号/子集/平行直接影响SQLite目录筛选与相关版本；总编/签字还参与完整性和来源身份匹配。价格分组的 `player_prices.discovery.card_key` 使用对应身份字段，但KnowledgeResolver/ScopedCardCatalog当前仅消费原entities/official_artifacts，不消费浏览器atlas覆盖。本首版不会自动重写已有价格分组。

- 每次成功覆盖按有效卡字段重算 `atlas.serial_status`、`autograph_status`，标记 `metadata_scope=effective_identity`；总编null始终未知，不把未知推成无编号；签字true/false/null分别保留属性是/否/未知，不声称实卡鉴真。
- 原始来源派生说明单列在 `source_atlas(scope=original_source_assertions)`；`original_identity`与原始实体/附件不改。链式覆盖始终保留最初来源，不把第一次纠错冒充原始证据。
- API `correction_effects` 与页面明确：原图片绑定需重新核对，实卡保持原样，价格分组未自动重算。来源不完整的“待核对”与有具体矛盾证据的“冲突”文案分开。
- RED→GREEN覆盖清空总编、签字true/false/null、改变子集、两次覆盖、有效字段筛选及原始附件SHA不变。相关新用例见 `test_knowledge_atlas_corrections.py`。

## 可审计的发布与回滚状态

没有新增表。`knowledge_atlas_lifecycle.py`以现有审计事件和不可变快照的提案包含关系派生状态，不根据可能过期的 `knowledge_version.status` 猜测当前发布。

- 提案列表和详情共用状态：待审核、待显式发布、依据版本过期、当前版本包含、回滚移除、当前版本未包含、驳回。
- 时间线保留全部提交、每次批准/驳回、首次发布、后续发布携带、回滚移除/恢复/保留；展示精确版本号/ID、操作人、时间、已有理由。原回滚接口不保存理由，因此返回null，不编造理由。
- 明示“当前版本包含”只表示快照成员关系；来源冲突、有效字段以及后续同字段覆盖仍以图鉴详情为准。已发布审核历史仍不可驳回改写。
- 新审计INSERT记录 `clock_timestamp()` 实际写入时间；历史记录不更新。独立复核发现旧默认now()可能早于实际批准，已加失败回归：原时间原样保留，并在矛盾时间事件标记 `transaction_time_uncertain`、页面提示历史事务时间先后不确定，不伪造历史因果时间。
- 为减少无关历史快照解压，历史查询按选中提案首次应用的版本下界裁剪；草稿不扫描无关发布历史。仍为两次批量读取，无每提案N+1查询。长生命周期/大量相关历史的生产负载尚未基准验证，纳入未来整合评审。

## 真实关键流程

CUA/IAB实际浏览器，本机QA服务只用历史冻结v26的真实单产品副本及 `/tmp` 隔离PostgreSQL。测试提案明确标“仅本地QA”，并非真实事实纠错。

1. 浏览器对真实Hansen Yang #14 / Base Prizms Mojo提出总编25→null，预览“未知”，提交并批准；列表显示待显式发布。
2. 通过本机QA的**既有真实 `/v1/knowledge/publish` 路由**发布v27：当前总编状态未知；展开原始来源仍为25；有效身份转待确认，旧图片绑定解除，双面筛选不再误收该条。页面显示首次发布v27、精确ID/操作者/时间/发布说明及价格分组未重算。
3. 通过既有 `/rollback` 路由回到v26：原总编25和两张来源图片恢复；提案显示“已回滚移除”，时间线为v27→v26。
4. 再回滚恢复v27：返回版本的存储status仍为rolled_back，但页面正确显示“当前版本包含”和“回滚恢复”；验证依赖当前指针/快照。390×844手机document.scrollWidth=390，控制台无错误。

截图：`verification/followup-published-metadata.jpg`、`followup-rolled-back.jpg`、`followup-restored-mobile.jpg`。本地版本收据 `followup-qa-versions.json`。QA服务已停止并清理自己的临时schema；测试集群随后停止。

## 独立复核与最终测试

独立窄范围复核报告：`verification/followup-independent-review.md`。没有发现其他F1/F2具体缺陷；独立链式元数据探针通过。发现的P2历史时间问题已由作者以 `test_legacy_transaction_timestamps_are_flagged_without_rewriting_audit` RED→GREEN修正。按一次窄复核方式，未再委托第二次复核作者修复。

独立网页组合运行曾有既有图片对照测试超时，单独重跑通过；作者最终完整相关网页组合全部通过。没有把该次波动隐藏或归因给未验证原因。

最终实测：
- 后端同前次10个相关文件：**168 passed，1个既有Starlette/httpx弃用警告**，6.50秒（含随后实际读取索引对齐及管理状态索引回归）。显式ATLAS_TEST_DATABASE_URL指向本任务 `/tmp/card-atlas-pg-socket` / 55569，不使用生产DSN。
- 网页：`node --import tsx --test tests/knowledge-atlas-manager.test.cjs tests/knowledge-browser.test.cjs tests/official-checklist-progress.test.cjs` → **28 passed**。
- `tsc --noEmit`通过；新/相关Python模块与测试 `ruff check`通过；`git diff --check`通过。
- 完整 `next build`通过，两个管理路由生成成功；日志 `verification/followup-build.log`。原独立ESLint配置缺失说明不变，未新增lint依赖；未声称全仓无关测试全绿。

## 整合边界

两项残余已收尾，不需要生产schema迁移。仍不能从此旧基线整体部署：实际读取适配已于父任务报告中固定：镜像 `atlas-readonly-20261030-023839`，完整ID及三文件SHA见 `deployed-readonly-alignment.json`。已按该实际schema7/两个索引/三条计数语句对齐，管理目录扩展为schema8；完整快照资源/35契约回放见后续对齐报告，目标环境预热及真实角色验证仍属于独立上线评审。另任务的部署许可不覆盖本任务。长历史查询生产负载未基准验证。没有待处理权限拒绝或未知写入结果。

实际读取基线最终对齐及完整35例/全库资源结果见 [对齐验证报告](deployed-alignment-verification.md)。最终冷建314.372秒、热打开1.463秒、缓存4,535,410,688字节；冷/热35例均零差异。无生产操作。
