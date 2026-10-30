# 安全收尾与上线评审交付

2026-10-30，隔离工作树 `card-platform-atlas`。本轮没有部署、构建镜像、push/merge、生产写入、schema迁移、创建生产测试角色或凭据；未改用户收藏/实卡/账本。父任务授权的本机隔离测试及生产只读对齐已完成。

## 最终修复与边界

`knowledge_api.py` 的管理router先经过既有 `require_admin/current_user`，保存可信会话用户。全部旧写入路由经 `_actor` 查询该用户的知识角色，header缺省使用会话身份、header不一致403。bootstrap同样只能初始化登录者自己，revalue审计使用真实操作者。editor提案、reviewer审核、publisher发布/回滚、admin兼容所有知识角色；全局app admin门槛保持。没有添加服务认证、权限配置或凭据。

核对现有 `sync_official_checklists.py`、`sync_official_history.py`、`publish_knowledge_release.py`、`verify_official_publication.py`：受信数据库程序直接调用仓储，内部选已有knowledge admin，不走HTTP actor头，工作流不受本次HTTP收紧影响。自动官方发布携带既有覆盖，不消费新人工审核草稿。

## 实际基线与最小覆盖

只读读取时间、容器与文件hash见 `verification/security/deployment-baseline.json`、`web-deployment-baseline.json`。API仍为 `atlas-readonly-20261030-023839`，完整digest `sha256:119b9ba3827c6db5ff544085374f12343174cae9df817d09251db413f9bddc65`，读取时healthy、成本worker=true。实际knowledge_api/repository与本任务起始基线字节一致，auth/auth_api也一致；读取atlas/browser_api字节一致。

网页实际 `card-platform-web:0.6.8-20261030-partialintake2`，digest `sha256:430c1ecee885561b3b847aa23a3e5cd63d804f4c91ecc4e4d5ff04cc6ca28651`，BUILD_ID `Ok9uElxPl77_Ai_PSowOb`。已定位该构建的本机冻结源码；线上139个编译文件（排除node_modules/cache/macOS元文件）与冻结standalone逐一hash，零差异。不能用旧worktree整体构建发布：冻结源码包含后续MCP/发现代理及其他既有功能。

源码准备脚本 `verification/prepare-compatible-source.py` 仅生成本机候选，不含docker/远端写入。最终候选 `.runtime/atlas-compatible-source-final`：

- API以实际上述digest为父镜像，只覆盖6个模块：`knowledge_api.py`、`knowledge_repository.py`、`knowledge_browser.py`、`knowledge_atlas_api.py`、`knowledge_atlas_corrections.py`、`knowledge_atlas_lifecycle.py`。其余生产模块/环境/卷保留。前3为修改、后3为新增；reader_api/atlas无需覆盖。
- web以实际冻结源码为基线，仅变更7个文件（6个运行文件与1个测试）：manage/page、atlas-manager.css、legacy/page、knowledge-atlas-manager、knowledge-atlas、api helper、manager测试；其余104源文件hash不变。api helper只追加，旧manage字节不变搬到legacy。
- `compatible-source-manifest.json`记录精确hash与全量源文件对照；本机namespace测试shim明确不进入镜像overlay。最终候选未构建镜像；已成功构建的是同7文件/104文件整合网页源码，在原始 `.runtime/atlas-compatible-source/web-source`，最终manifest网页hash相同。

只读现行知识指针为 **v27/rev1861**，id `0710ef3f-c147-45bc-9619-9d8c83a6887f`、checksum `87a77700583d48bad400a9236cdfa57637c7bdfb345b34acadf1bf698622690e`；线上795审计/27版本/2位knowledge admin。见 `current-knowledge-pointer.json`。本机v26历史快照仅作可复现回归，代码上线不携带该数据库快照、不改知识指针。正式操作前应重读当时最新指针，不固定回到v26或v27。

## 持久化与恢复影响

不需要新数据库schema。提案/审核/应用事件复用现有 `knowledge_audit_log` 的JSONB（atlas_submit/review/applied）；发布覆盖进入不可变 `knowledge_version.snapshot.atlas_corrections`。审批增加workspace revision，显式正常发布后生效。原始来源证据不修改。

安全代码恢复路径：网页可独立回目前partialintake2；API保留经验证的六模块兼容内核。API故障前滚恢复/重建该固定hash源码覆盖包，保留其他生产模块。旧119b只读镜像不了解覆盖，因此不能成为新功能的普通API恢复目标；不依赖“记得停发布”。上线前必须将兼容内核的不可变可恢复镜像实际构建、校验和演练成功，不能将本机源码包当成已存在镜像。

网页回退或兼容API恢复不删提案；待审/已审未发布提案继续保存在审计中，后续前滚界面可回读。自动发布保持已生效覆盖、让新人工草稿留待显式发布。真实知识版本指针回滚则按选择快照改变有效身份：回到不含纠错版本会暂时撤销该覆盖，但保留提案/审核/历史且不会自动复活已消费提案；显式恢复含纠错版本可以恢复有效字段。这与代码故障恢复不同，不应在代码上线中回退知识数据。

## 验证结果与证据

- RED：真实auth_session复现viewer冒充publisher得到200、bootstrap代他人授角色得到200；记录 `session-red-summary.json`。没有覆盖current_user。
- 最终相关后端/认证回归 **179 passed**，日志 `backend-regression.txt`。真实cookie和Bearer、缺会话/非法/撤销/过期/disabled/全局非admin、viewer/editor/reviewer/outsider伪造publisher/admin、合法角色隔离和无知识写入、合法publisher回滚/admin恢复均覆盖。
- 最终兼容源码在新解释器加载，六模块路径/hash与manifest/当前源码一致（`compatible-module-imports.json`），session+corrections **32 passed**（`compatible-api-tests.txt`）。创建→审批→发布→旧网页协议再次发布→自动发布→读取覆盖保持；另创建pending/approved未发布提案，验证保留且未生成atlas_applied。
- 合成历史负载：2万无关审计、2千提案、500后续版本，100条分页约1130ms、单图鉴501版本历史约859ms。本机结果，非目标机SLO，见 `history-load.json`。没有为性能新增生产索引/schema。
- 实际线上冻结源码+最小web覆盖，**242网页测试通过**、tsc通过、Next生产构建成功；保留既有globals.css的autoprefixer警告。构建第一次字体网络受限失败，获准联网获取既有Google字体后成功。原工作树28项相关网页测试第一次有既有图片查看器测试瞬时失败，最终整组 **28/28通过**；整合冻结源码242项也全通过。不能称全仓后端测试全绿。Ruff与diff-check通过；仓库无独立ESLint配置，不宣称独立ESLint通过。
- 真实浏览器使用现有密码登录路由建立cookie（无auth override），在本机历史单产品16,489条目录中筛选Hansen Yang #14的71行，提案1→未知、批准草稿、保留legacy入口显式发布至本机v27/rev3，浏览器返回保留筛选/card_id；当前有效总编未知、原始来源标注、身份待确认及发布审计均回读正确。该本机v27与生产v27不是同一版本。截图 `real-session-preview.jpg`、`real-session-approved-draft.jpg`、`real-session-published.jpg`，DOM `real-session-published-dom.txt`。旧页初次QA缺coverage/import_batch只读辅助表；补齐命名临时schema后完整流程成功，不涉及生产schema。
- 前轮全量2,169,016来源目录冷建/预热及35旧契约零差异、~619MB峰值RSS/~4.535GB缓存仍是可复现v26证据（`deployed-alignment-verification.md`），本轮仅auth/router修改未改派生构建逻辑。不能冒充现行v27目标机完整负载验证。
- 独立安全审查见 `verification/security/independent-security-review.md`：审查者自行96项非DB回归通过；Important测试缺口已补齐并复核关闭，无遗留Critical/Important。

## 可放行的下一步与仍需批准的目标动作

本地代码安全阻断已修复，可进入单独候选构建/上线评审，尚不能直接认定目标环境可切换。

1. 获准后构建固定digest的兼容API内核镜像及基于实际冻结源码的web镜像；验证只覆盖清单、保留全部现有功能/环境/卷、成本worker=true及既有视觉/搜索配置。保留独立web回退镜像和兼容API前滚恢复镜像，演练后才允许新提案生产写入。
2. 若需目标机负向角色/历史写流程，用同一候选镜像、独立临时数据库/数据目录、无公网暴露进行测试；需明确批准这一新测试部署，不自行启动。复用已有auth机制/临时测试数据，不对生产创建样本、凭据或扩角色。生产当前只有knowledge admin；本机完整角色矩阵不冒充生产viewer/editor/reviewer验收。目标生产既有合法会话只读检查权限/列表/详情可不增角色。
3. 在目标环境对当时现行快照（目前v27）执行完整目录/35契约、冷建/预热、并发读及磁盘/RSS/健康验证，预留旧新缓存共存与临时构建空间，不能只按单个4.535GB缓存预算。始终保留最新知识指针/版本证据，无数据回退。
4. 角色/恢复/资源验证和独立复核通过后，才可请求用户批准具体镜像切换。本任务未获得这些部署许可，不已开始或暗示完成。
