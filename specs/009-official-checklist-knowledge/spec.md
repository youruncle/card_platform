# 自动官方清单知识与估值查询

Created: 2026-10-04
Status: Authorized for implementation
Input: 自动录入近30年 Topps/Panini/Upper Deck 篮球新产品官方知识，无人工审核；接通知识查询和估值，用现有卡片验证，显示进度百分比。

## User Scenarios & Testing
### US1 官方知识自动录入 (P1)
用户运行同步后，官方篮球清单全部自动采集、可解析事实自动入库发布，不需人工确认。每产品保留原文件、来源、日期依据、解析状态与失败原因，可恢复重试，重复导入不重复数据。
### US2 知识真正参与查询和估值 (P1)
同系列/年份/卡号/球员查询得到候选卡种与版本；唯一一致事实补全缺失字段，冲突或多版本不猜测。卖家别名辅助召回，成交与图像/品相门禁保持。已有明确人工身份不被覆盖。
### US3 进度与回放 (P1)
工程百分比、发现产品数、来源获取/解析/发布数量、失败及缺日期数量分开展示。冻结目录后覆盖率分母固定；未完成来源不算成功。已有持仓只读回放输出命中、补全、冲突与前后差异。

## Requirements
- FR01 范围为 Topps/Panini/Upper Deck 官方入口的篮球产品，发行窗口 1996-10-04..2026-10-04（含边界）。未知发行日期单列；发行年份/赛季仅作为发现候选，不能伪装为真实发行日期。
- FR02 保存所有发现的候选官方资料，包括日期待确定资料；有明确窗口证据的产品计入已核实窗口分母。用户要求全自动，无人工审批节点。
- FR03 官方事实自动信任；解析结构、页数、字段归属、哈希、重复、冲突做自动检查；无法识别的数据保留原文并报告，不编造事实。
- FR04 球员/卡号/特卡/平行/限编必须保留产品、年份、运动、来源范围。不能把特卡清单的卡号当全局唯一，不能枚举未经证明的版本笛卡尔积。
- FR05 原始下载内容不可变，版本与解析器版本可追溯；来源失败独立重试，不撤销已经成功的产品。
- FR06 自动发布不批准其他来源的草稿，不改真实卡片、交易或历史估值；知识发布本身已获本次用户授权。
- FR07 查询输出引用来源与知识版本。稀缺性、印量、签字属性不从营销词无依据推断。
- FR08 已有卡片验证必须执行真正的运行时查询，不能仅统计入库行数。至少包含586及跨系列/年份/不同卡号负样本。
- FR09 进度可在知识库管理入口查看，持久化运行记录可复查。分母未确定时显示发现中，不编造百分比；异常/部分成功不显示100%成功。

## Success Criteria
1. 每个已发现产品有最终状态和可重试原因；所有成功事实有来源定位。
2. 自动运行到知识发布无需人工审核；重复运行无重复实体。
3. 官方知识可使586通过产品/卡号查询补全Roses，且错误系列、错误球员或未知平行不能误补。
4. 对现有有效持仓保存只读前后查询回放与统计，不伪称已重新估值。
5. 进度计算可被测试复算，成功覆盖率与已处理比例分离。

## Assumptions
近30年以实际发行日期优先；未能取得日期的官方当年/近年清单仍保存并单列日期未知覆盖，不能承诺穷尽不可访问目录。复用现有知识表与API，不另建并行知识系统。当前工作区大量未提交修改属于用户，保留并在原工作区窄范围增量实现。

## 30年扩展约束
覆盖各厂商官方篮球目录内品牌，保留官方归属证据；其他运动排除。跨worktree共用PostgreSQL发布锁。以已发布v11为基线追加，不覆盖持仓、估值或无关草稿。

## 2026-10-21 修订：按年份建设图文与向量知识库

用户明确以官方及可靠外网资料为起点，按年份逐步建设独立于持仓的知识库，文字和图片同时补齐；增加卡牌向量相似检索。以下为本轮规划要求，后续执行状态以 tasks.md 为准，不能当作已采集或已上线结果。此前固定日期窗口和 v11 是历史批次约束；新增年度队列以实际执行时的正式产品资料为准。

- FR10 建设目录为年份/赛季、厂商、正式产品、子系列/版本、具体卡片。Topps、Panini 为主线，沿用篮球范围与 1996–97 起的历史目标；持仓仅作附加验证，不决定目录、收录或执行优先级。
- FR11 每个年度先建立来源可追溯的产品母表，再逐产品推进。默认从 2025–26 向历史逐年补充，更新赛季正式资料进入增量队列；预览、发行日期未知、目录发现未完成均独立标记。
- FR12 每个产品同步建设文字、图片、图文关系和派生检索索引。文字包括产品/授权、卡号与球员、基础卡、特卡、平行、签字/物料、限编/赔率及其作用域；不展开无来源组合。
- FR13 图片建设分别覆盖子系列设计、平行差异和具体卡片正反面。每张图记录来源页面与原图、哈希、获取时间、侧别、产品/赛季、对应卡片或适用版本、核验状态和访问范围；局部图可回溯原图。
- FR14 官方设计图、核实实卡、未核实图片分型。正反面同卡关系有依据；缺图保持部分覆盖。包装图、设计示例、同一张卡的多张图不能替代整套卡片图片覆盖。AI 生成图不作版本实证。
- FR15 官方优先；历史缺口可使用可追溯第三方目录和原始实卡图。来源的可读取、文字可采集、图片可采集与使用范围分别验证；失败记录并可恢复，不冒充官方事实。
- FR16 为文字、正面、背面及必要局部图建立可重建向量索引；卡号等精确字段保留结构化与关键词索引。向量绑定实体/媒体、来源哈希、模态、模型/预处理版本、维度、知识版本和可见范围。
- FR17 检索支持向量候选与结构化/关键词候选融合、证据重排。已确认字段作过滤，未确认字段不能作为排除正确候选的唯一硬约束。结果展示图片、差异、来源、知识版本；相似度不能单独确认身份、版本、同实卡、品相或价格。
- FR18 不同编码器的向量不可直接比较；文字搜图片须使用经验证的跨模态空间或分别召回融合。原文/原图不被向量替代；换模型可重建、切换索引版本及回滚。
- FR19 文字覆盖、设计图覆盖、逐卡正反面与配对覆盖、向量覆盖分别统计，分母来自已证实的目录或可用资产。存在未完成发现时不能宣称完整年度覆盖，文字齐全而缺图的产品不能标图文全覆盖。
- FR20 检索评测按年度目录独立抽样，覆盖相邻版本、不同球员/赛季、反光/旋转、裸卡/卡壳、图库外卡；同图/近重复自匹配与泛化结果分列。报告 Recall@K、版本混淆、延迟、资源与失败，不预先承诺未实测准确率。
- FR21 复用现有知识版本、来源和查询边界；保留私人图像权限及既有业务数据。每批增量发布保留索引与事实一致性，官方结构事实继续走现有自动检查流程，未知/冲突候选不直接变成正式事实。

本轮验收：spec、plan、tasks 的范围和顺序统一，旧的持仓驱动路线已替换；正式图文与向量交付以各任务真实验证为准。


## 2026-10-22 已批准：知识目录、卡牌详情与图库改造

用户已确认聊天中的目录＋详情＋图库方案并要求开始改造。此轮为现有知识浏览能力的实现，不重复创建规格；自动采集、向量检索及历史内容补齐仍按原任务维护。

### US4 看清已收录知识（P1）
无需输入关键词即可看到所有已发布厂商、厂牌/系列与赛季，以及卡牌版本事实、设计参考图和逐卡图片的分项数量。目录支持厂商、赛季、名称/别名、有图筛选；知识说明可按实体类型浏览和检索。

### US5 找到卡牌与版本（P1）
从系列进入产品说明、卡牌清单、子系列/平行版本、图库和来源；按球员、卡号、版本检索并分页。卡牌详情显示身份、限编、RC/签字/物料、来源定位、同作用域版本及正反面缺口。不推断未知字段或跨作用域卡片归属。

### US6 查看真实参考图（P1）
网页和手机端可查看缩略图、放大、缩放/旋转及正反面/版本对照；明确区分系列设计示例、已核实实卡、侧别未知与缺图。只展示发布快照明确引用的可见资产，保护私有图片，不拼造配对。

- FR22 浏览默认基于不可变发布快照，汇总实体和清单附件；不以实体表行数冒充卡牌事实数，不展示未审核工作区草稿。
- FR23 产品按明确厂商/品牌和赛季归档；历史厂商、无年份产品和未知归属不能被隐藏或猜测。
- FR24 列表服务端分页，搜索与筛选重置页码；网页和手机同一合同；保留查询上下文，取消/忽略过期响应。
- FR25 图片通过鉴权接口提供本地校验字节；限制路径、核对哈希、显式绑定卡牌，禁止任意外链代理或目录遍历。缺失/损坏分别报错。
- FR26 文字/图片/配对覆盖分列，分母未知不显示全量完成百分比。快照附件损坏、缺失或解析失败须可见，不能悄悄显示为完整。
- FR27 网页原管理功能保留至 /knowledge/manage；普通浏览不需要手填操作人UUID。沿用现有权限，不扩展匿名/普通账户数据访问。

## 2026-10-22 录入闭环与SOP（用户要求）

### US7 一批知识必须有始有终（P1）

管理员冻结年度产品范围后，能看到每个产品已实际搜索哪些来源、抓到什么、谁审核、哪些资料已发布、剩余缺口的证据；会话中断后能继续。完成报告不能再包含未经搜索却声称本批已完成的产品。

- FR28 录入遵循docs/knowledge-intake-sop.md；云端发布知识为唯一主库，不更新本地运行知识库。
- FR29 每批从云端冻结完整产品清单、目标维度、截止时间和基线版本。范围缺项、重复、未搜索、待审核或可用未发布资料必须阻止完成/封版。
- FR30 每个缺图产品实际执行厂家、专业资料站、公开实卡来源三类检索；未取得可用卡图时再执行经销商原厂资料路线。记录真实查询、结果捕获、时间、哈希、候选处置。不同域名转载同一内容不能冒充独立来源；未搜索不得变成外部阻塞。
- FR31 文字、设计图、逐卡正面、背面、同实物配对、向量分别记录覆盖与未知分母；正面可用而背面缺失时应先录入正面。无法取得的内容必须有来源失败证据和恢复动作，不能宣称全网不存在或知识完整。
- FR32 每个解析异常和所有取得的候选都有明确审核结果；可恢复内容本批处理，来源冲突/缺行与解析缺口分开；来源、身份、图像及审核对象hash不一致时不能发布。
- FR33 发布前检查云端基线、受控变更、所有来源/媒体/索引一致性；发布后实际云端回读并验证查询、文件、配对、旧版本与幂等。上传/发布响应不代替验收。
- FR34 未来录入工作台持久记录任务步骤、证据、审核、发布差异、租约和失败恢复；批次处理完成与内容全覆盖分别显示，保留现有权限和人工事实。

本轮验收：126个已知产品文字来源均经过核查；53个原缺图产品全部完成必需路线与候选处置，未搜索=0、待审核=0、可用未发布=0；全部可核验增量发布云端并实际复验。真实外部缺口逐项列出，不把它计作知识齐全。录入模块本次交付设计合同contracts/intake.md，尚未要求交付新应用模块实现。

验收：厂商→系列/赛季→卡牌清单→卡牌详情→图片全路径；中文别名、跨年/同卡号/缺图/错误来源负样本；网页窄屏和手机鉴权图片实测。此轮实现不包含部署或新增图片采集，不修改持仓、价格、模型及生产worker。

### 2026-10-23 九产品深化与按需印刷目录续批

- FR35：冻结云端v22/revision1837/checksum9446f4c1f3fc7a303f9780700a025dfaef437bee50645ccf91de4f0acca964e6；深化2023–24 Chrome Hobby/Retail、2024/25 Chrome、2025–26 Chrome/Topps Basketball/Chrome Updates及2023–24/2024–25/2025–26 Prizm共9产品。以来源证明的子系列、SKU/盒型记录平行、限编、赔率、变体和签字物料规则，不把产品规则展开为未经证明的逐卡事实。
- FR36：Topps NOW与Panini Instant按2023/2024/2025起始标签逐年检索官方归档和可靠补充资料。年度目录可建立来源索引，但不将当前在售页声称为全年完整清单，不因URL年份/卡号替代页面实际字段；印量与每卡限编分开。未证明存在的产品不能新造发行实体。
- FR37：本批已有图片仍逐项执行厂家、资料站、实卡三条路线并审核全部候选；来源图片、具体身份与同实物配对分开。云端已有清单、人工属性、旧图和其他发布实体保持原值，只有审核的新增事实/媒体与索引元数据可变。新增目录项必须有封存来源与范围审核，不缩小冻结工作分母。
- FR38：同批源/图/向量封版后仅发布云端并实际回读。未搜索、待审核、可用待发布均0才可报告处理闭环；真实背面、归档、来源冲突等内容缺口逐项保留。未完成的年度全逐卡图片、应用融合召回和独立准确率评测继续保持原任务未完成。

### 2026-10-23 程序化实卡采集第一版（用户已要求开始实现）

US8：管理员运行后台命令，从云端已发布清单生成300–500款分层试点任务，程序自动检索公开实卡、下载原图并留下可恢复状态和证据，审核后导出既有知识媒体合同。减少对话式逐张搜索/搬运；本版不代表全库图片已补齐。

- FR39 以运行时云端版本/checksum及实际清单附件冻结范围，按产品轮转选取缺图卡款；不得从持仓或本地运行知识库取分母。清单附件先验证SHA，保留产品/赛季/原始子系列/版本和来源定位；未知字段不推断。
- FR40 云端PostgreSQL持久保存批次、任务、候选及审核事件；任务支持租约、过期恢复、有限重试和幂等。候选、证据与临时本地材料均不是已发布知识；命令不会写本地运行知识库。
- FR41 首版提供卡淘公开搜索与商品图库适配器，保存请求头/参数、时间、响应字节/SHA，按站点限速，403/验证码/空壳响应作为阻塞。图片链接、下载可解码和具体卡款确认分别计数；不凭图片顺序推断正反面。
- FR42 审核针对不可变候选hash，接受/拒绝均需理由和逐图角色；确认身份须与冻结清单逐字段一致，同实物配对须来源及视觉依据。未审核不能导出ready媒体；审核输入或文件改变使审核失效。支持结构化审核文件；模型/人工审核结果使用同一合同，不自行调用搜索模型凭空认定身份。
- FR43 导出可交给现有媒体/向量/发布工具的public_reference资产与台账，审核通过的正面可单独导出；待审、缺图、来源失败、可用未发布独立统计。导出不等于知识发布；云端发布仍遵守SOP、多来源、基线保护及回读门禁。
- FR44 首版交付CLI、数据库迁移、真实云端试点和操作说明。测试覆盖错产品/年份、空壳响应、恶意链接、超量响应、损坏文件、重复运行、过期租约、旧审核及未审导出；试点300款冻结队列，实际网络请求使用明确卡款/页数/候选数量上限。


## 2026-10-27 获批首片：离线增量计划与可信覆盖

用户已批准实现；本节为既有 009 规格的只读首片，不替代知识录入 SOP。

### 目的、选择与边界

用户已选择：官方目录主干＋第三方补规则＋按缺口补图；先确定性提取，保留来源，分开基础卡／版本／实物，冲突不覆盖，内容哈希缓存，模型仅用于异常。

首片只交付一个**离线、只读生产数据**的 CLI：从已导出的云端快照及已有捕获生成 `plan.json`、`coverage.json`、`report.md`。输出“哪些跳过、哪些有变化、哪些缺材料、哪些待审”，不生成可发布包。首次和重复运行 LLM 调用都为 **0**；异常进入人工／后续模型审核队列。不是整个 12–20 天 MVP。

已考虑方案：①从头实现采集/工作台/发布，范围过大；②直接重跑旧批次脚本，会重算且存在硬编码；③在既有编译器和发布闸门前加只读规划层，采用③。无 DB migration、新 API、网络采集、图片下载、向量生成、自动跨源合并、生产写入、push 或发布。

已知云端历史观察：2026-10-27 01:57 UTC，v23/rev1853/checksum `78e1bf3a8400530c264b3ccc5a1f376160debb1d785ac7db75e1d69ce5a730fc`。不是本次完整快照，也不是未来发布基线。浏览汇总曾 503/preparing，coverage registry 为空；CLI 不依赖它们。

### 已核对的复用与禁止误用

| 现有实现 | 首片处理 |
|---|---|
| `knowledge_text.compile_source(product, receipt, output_root)` | 在独立缓存子目录调用；已有源身份/字节/SHA校验及来源定位；官方 PDF/XLSX/Panini CSV 复用 `official_checklists.parse_document`，保留特殊 Mercury/UConn 分流 |
| `knowledge_text.compare_card_facts(cards, previous, *, normalize_participant_order=True)` | 仅对同一产品、同一来源作用域做变化摘要；不是全局去重器，重复身份先检测，不让 dict 覆盖隐藏冲突 |
| `PARSER_VERSION=official-layout-v8-release-season`、`COMPILER_VERSION=official-text-compiler-v1` | 进入缓存依赖键；升级使缓存失效，但不自动调用模型 |
| `knowledge.snapshot_checksum(snapshot)` | 校验导出的完整发布快照；文件 SHA 与语义 checksum 分开保存 |
| `ScopedCardCatalog` / `product_key` | 保留其严格作用域作为回归；不使用模糊产品名归一化作为全局合并主键 |
| `scripts/supplement_knowledge_sources.py` | 复用第三方 `checklists.json` 的产品、facts、capture 字段约定；**不调用 stage**，它写批次且有历史硬编码 |
| `publish_knowledge_release.validate_intake_audit` | 沿用 focused-v2 将来发布闸门，首片只报告缺少哪些输入；**不调用 publish**，不宣称计划已满足发布 |
| `build_annual_inventory` / `KnowledgeBrowser.summary` | 沿用“来源已知≠完整年度”的口径；不创建浏览索引，不以其数据库行数充当全集分母 |

### 输入合同（新版本号均为首片本地文件合同）

所有输入路径相对 `--input-root`，resolve 后须在其内，拒绝逃逸/symlink 逃逸；只允许输出/缓存目录写入且不得与输入目录相同、互为祖先或包含输入文件。UTF-8 JSON，拒绝重复键、非有限数值。无凭据字段、无连接字符串。

1. `baseline.json` (`contract=knowledge-readonly-baseline-v1`)：`captured_at`；`workspace_revision:int|null`；`published:{version:int,version_id:str,checksum:64hex}`；完整原样 `snapshot`（现有 entities/relations/official_artifacts）；`artifact_files:[{checksum,path}]`；可选 `sources`（原始来源行，只作为证据，不伪造）；`coverage_registry:{status:"available"|"unavailable",rows:[]}`。缺 registry/来源行不等于零覆盖。快照 checksum 不匹配为致命错误。缺完整快照不接受浏览汇总替代。
2. `scope.json`：`contract=knowledge-intake-plan-scope-v1`；`season_starts:[2023,2024,2025]`；`existing_product_keys:[...]`；`annual_research_keys:[...]`。产品 key 必须唯一且存在于完整快照的对应官方 Topps/Panini 产品中。目标必须有 `attributes.source_key`、manufacturer/product/sport/season_start/season_end；不猜单年跨年、联盟或同名子品牌。年度检索单元只报告待检索，不虚造产品。
3. `captures.json`：`contract=knowledge-source-inputs-v1`，`items:[{input_id,product_source_key,adapter,product,receipt,facts_path?}]`。`adapter=official_document_v1` 使用现有 product+receipt 字段（source_key/title/publisher/source_url，receipt另含 status/http_status/captured_at/path/sha256/bytes）；`specialist_facts_v1` 的 facts_path 指向现有 checklist research 形状（products→source_key/page_url/site/source_authority/evidence/facts/limits）。每个 fact 保留 field/value 和原始 JSON pointer，必须有支持它的来源 locator，否则排入 review_needed，不把 JSON pointer 当作原文证据。

不支持任意第三方 HTML 自动理解；未知适配器保留 `blocked:unsupported_adapter`。缺捕获、403、缺 artifact、坏哈希和解析异常分别记录。错误页不是成功资料。缺原始 source row 在 release-readiness 中显式列出，不阻断已经可验证的快照统计。

### 算法与身份边界

- 每个候选保留 `candidate_id,product_source_key,subject_key,field,value,source_url,source_sha256,locator,adapter_version`；URL 仅折叠 scheme/host 大小写和去 fragment，不删 query，不擅自合并 URL 别名。值用带类型的 canonical JSON；保留原值，不折叠字符串与数字，不把空值当否定。
- subject_key 对产品级第三方主张是 `product:<source_key>`；官方卡行是 `card:<source_key>:<section/card_number/player/source_variant_label 的 canonical JSON hash>`，不跨源折叠；已有明确 subject 绑定原样保留。去重 identity 是产品原 source_key＋subject_key＋来源 URL＋来源 SHA＋field＋canonical value；locator 汇集保留。对 `supplementary_evidence` 全列表比对；有完整相同证据则 `duplicate`。缺旧 provenance 则 `review_needed:baseline_provenance_incomplete`，不能把旧值当新事实。
- 同产品同 subject_key 同字段同值、不同来源是 `corroborating`，仍保留独立记录，不无条件合并；同产品同 subject_key 同字段不同值是 `review_needed:conflict`。若 field 的 value 只有产品级粗粒度（如 parallel_rule 对象），保守待审，不凭字段同名宣称真实语义冲突。原事实/人工修订不变。
- 官方行 ID 与差分是 **source-local explicit variant records**。`section/card_number/player/source_variant_label` 相同但事实不同须全部保留为冲突，不能让 `compare_card_facts` 覆盖一行。跨源匹配不产生统一卡 ID。
- 基础卡身份、版本身份、实物身份分列：能保留已有绑定则原样保留；不能由官方版本行推导全局基础卡 ID，不能由相同球员/卡号/图片推导同实物。未解析的基础卡/variant/physical ID 均 null。绝不展开基础卡×平行笛卡尔积。
- 提取缓存键＝SHA256(canonical JSON of adapter+adapter version+parser/compiler versions+输入字节 SHA+精确产品作用域+提取参数)。缓存 envelope 同时保存 payload SHA，读取时验证；corrupt entry 仅 stderr 报告并安全重算（不影响稳定输出），覆盖只发生在独立缓存。提取与 baseline 差分分开，快照变化每次重新比对；缓存不暗含审核批准。
- 相同来源字节/依赖且有完整缓存时不再解析；首次缓存未命中可确定性编译。changed source 才触发新的提取。所有模型/网络/DB 调用严格为零；没有“缓存未中就花模型 token”。
- 相同输入与版本的计划/覆盖/报告输出 byte-for-byte 相同；稳定排序、JSON sort_keys、UTF-8、末尾换行，不写当前时间/绝对路径/cache-hit到结果。缓存命中/解析次数仅 stderr 运行诊断；`llm_calls=0` 可写入稳定报告。

### 输出与覆盖口径

`plan.json`：合同 `knowledge-intake-plan-v1`，baseline 身份/输入 hashes/版本，冻结 work_scope，按稳定 candidate_id 排序的 source_actions 与 fact_decisions，issues（代码+来源+恢复动作），review_queue，`llm_calls:0`，`publishable:false`，`focused_v2_required_inputs`。

source_actions.status：`unchanged|changed|new|blocked`；fact_decisions.status：`duplicate|new_candidate|corroborating|review_needed`。这不是已审核/已发布状态。

`coverage.json`：合同 `knowledge-coverage-report-v1`，`as_of=baseline.captured_at`，分开全体已知基线/当批范围，分 season_start 和 manufacturer；至少包含：
- `known_product_count`、`work_scope_product_count`、`annual_research_unit_count`；未知年度总产品分母 `annual_product_denominator:null`，`annual_discovery_complete:false`。
- `observed_verified_checklist_records` 为成功校验的 artifact 行数；任何必需 artifact 缺失时全集 `checklist_variant_records:null`，并列 `missing_artifact_count`；全部完整时才给该**已知基线**总数，不称基础卡数。
- `distinct_base_cards:null`、`full_card_image_denominator:null`、`full_card_image_percent:null`。首片不解决权威跨源 base-card 对齐，故保留 null。
- `reference_image_records`（按 asset_id；同ID不同hash为冲突）、`unique_image_hashes`、`image_type_counts`；分 design 与具体卡图；`declared_specimen_pair_records` 只统计发布快照明确声明、两面不同SHA且同pair_id的记录，不称新核图；`freshly_verified_specimen_pairs:null`。
- registry 状态独立显示；空 rows 不覆盖快照事实，更不输出“覆盖率0%”。工作流本批未登记搜索/待审/外部阻塞分列；本 CLI 不开展检索，无真实检索审计时记 `search_evidence_not_provided`，不声称历史上从未搜过，也不能自动归类为外部阻塞。

`report.md` 简短中文摘要、输入时间/版本、缺口及恢复动作，明确“只读计划，不是录入完成”。不复用历史 702430/858/46 等数字作为本次实测。

focused-v2 缺项清单须包括完整年度 baseline_capture、frozen_work_scope、原始 owning source、逐目标真实三路线搜索、text/source/image review、source_fact_audit/fact_publication_map、sealed files 与 baseline revision/checksum；保留原实体/source/media/index，coverage=[]、不覆盖旧 assertions。首片输出不是 release/bundle.json，也不能绕过现有闸门。


### 首片验收

fixture 双跑三个输出 byte-for-byte 相同，第二次官方解析为 0，模型调用始终 0。基线变化重新计算差分。生产写入、网络采集、自动跨源实体合并、图片下载、模型执行和发布均不在范围内。
缺少真实完整云端快照时仅能报告 fixture 验证，不可报告真实覆盖已测或知识已录入。
