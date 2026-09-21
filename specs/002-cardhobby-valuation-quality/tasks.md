# Tasks: 卡淘估值覆盖与证据质量

唯一执行清单；R0完成只读部分，2026-09-21用户已批准R1–R4实施及全部达标后直接批量写回。所有源码路径以仓库根目录为基准。实施结果与未完成项见verification.md。

## Phase 1 — Setup

- [x] T001 阅读宪章/工作流并保存既有文件哈希到 `work/audits/valuation-design-20260921/initial-file-hashes.json`，建立本目录唯一spec/plan/tasks。（FR-001/012）

## Phase 2 — Foundation

- [x] T002 在 `specs/002-cardhobby-valuation-quality/export_baseline.py` 实现和执行只读冻结导出，保留519张卡、身份版本、扫描结果、3373条卡淘候选和旧任务；输出 `work/audits/valuation-design-20260921/frozen-v2/`。（FR-001/002/003）

## Phase 3 — US1 可复现基线 / R0→R1

目标：能区分状态并追踪每一步与实际资源消耗。独立验收：同冻结文件汇总一致；缺失成本为null；审计对生产零写入。

- [x] T003 [US1] 在 `specs/002-cardhobby-valuation-quality/baseline-summary.json` 及 `research.md` 汇总当前/历史估值、失败阶段、耗时口径与缺失成本；私有逐卡明细为 `work/audits/valuation-design-20260921/card-ledger.json`。（FR-002/004/005）
- [ ] T004 [US1] 在 `scripts/audit_cardhobby_valuation.py` 增加离线和只读shadow入口；先在 `apps/api/tests/test_valuation_audit_readonly.py` 验证输出目录已存在拒绝覆盖、多个owner停止、只读事务和任何mutation调用失败，再实现最小读取。离线模式禁止网络和模型；shadow仅写work/；与生产共享有效身份纯函数，不调用worker.run/_calculate。（FR-001/003/012，SC-001/007）
- [ ] T005 [US1] 在 `apps/api/src/card_platform/valuation_trace.py` 定义data-model.md的七段trace及no-op sink，接入 `image_service.py`、`vision_routing.py`、`valuation_worker.py`、`comparable_image_verifier.py` 的模型调用边界；先在 `apps/api/tests/test_valuation_trace.py` 测重试、缓存token、usage缺失=>cost=null、失败耗时和价格版本，再接入只读shadow，不改生产计算行为。（FR-004/005，SC-006）
- [ ] T006 [US1] 在 `apps/api/src/card_platform/ingestion/cardhobby.py` 和 `market_data/cardhobby_provider.py` 接入可选trace sink，保存原始响应/详情与逐行拒绝、query/page/截断去向；在 `apps/api/tests/test_cardhobby_audit_trace.py` 用超时、零结果、坏行和候选下游全拒绝测试区分状态。按影子预算运行并写 `work/audits/valuation-design-r1/`。（FR-004/005/007）

## Phase 4 — US2 独立复核与身份质量 / R0→R1→R2

目标：原图与商品证据分别验证，不让旧模型结论自证。独立验收：已知异卡拒绝、字段角色不混淆、缺证据区间为null。

- [x] T007 [US2] 在 `specs/002-cardhobby-valuation-quality/sample-reviews.json`、`samples.md` 记录14张直接图像复核、29张候选图、页面可访问性；生成60张固定分层集 `sample-set.json`，不把历史观测范围冒充gold。（FR-006/008/011，SC-002部分）
- [ ] T008 [US2] 按 `sample-set.json` 完成余46张原图/候选/详情审查及可取得证据补齐；在 `apps/api/tests/test_valuation_identity_provenance.py` 以#97清单203、#486GR-5、#4坏背面、confirmed覆盖投影建立失败测试；只在 `image_service.py`、`domain/card_identity.py`、`valuation_worker.py` 必要边界补字段来源/角色和版本绑定。独立复核者先看证据再解盲；无法获取时保留unknown到 `sample-reviews.json` 新版本。（FR-003/006/008/011，SC-002/005）
- [ ] T009 [US2] 在 `apps/api/tests/test_comparable_version_texture.py` 固定#383/92031221星纹异版和#486三候选异卡为拒绝，#519彩虹反光为不因反光直接拒绝；在 `comparable_image_verifier.py` 与 `formal_cardhobby_gate.py` 最小修复版本纹理/命名平行和旧视觉结果哈希绑定。证明修复前失败、修复后通过，不以普通same或同/75绕过冲突。（FR-006/009，SC-005）
- [ ] T010 [US2] 在 `apps/api/tests/test_cardhobby_detail_evidence.py` 固定#6详情免责声明、#305小花点、正常无明显损伤反例与零出价/挂牌/未来时间；在 `ingestion/cardhobby.py`、`formal_cardhobby_gate.py` 保存详情并区分具体损伤、模板免责声明和缺失。冲突候选不定价；付款证明未知不得升格。（FR-007/008/009，SC-005）
- [x] T011 [US2] 在 `apps/api/tests/test_player_alias_unicode_evidence.py` 复现#135西里尔Е、ASCII姓名仍无中文及同姓异人反例；在 `domain/player_aliases.py`、`marketplace_aliases.py`/已发布知识审核流程加入有两条不同卡淘商品支持的Trae/特雷杨/吹杨实体别名与受限OCR纠错候选。先用于检索，不自动改原卡身份或把别名当证据。（FR-003/006/009）

## Phase 5 — US3 固定预算召回与校准 / R3→R4

目标：用相同样本、预算与门禁提高最终合格证据，之后校准价格。独立验收：指标满足spec；unknown不进入误差分母；保留集不调参。

- [ ] T012 [US3] 在 `apps/api/tests/test_cardhobby_recall_budget.py` 建立“第一页60条、前三个标题候选最终全拒绝、第二页存在同款”的离线响应夹具；在 `ingestion/cardhobby.py`、`market_data/cardhobby_provider.py` 和 `valuation_worker.py` 最小修改停止/截断衔接。采用最终合格去重证据驱动停止，保留有界词/页/图片预算并记录耗尽原因；#102/#135回放验证非门禁放宽。（FR-004/009/010，SC-003/006）
- [ ] T013 [US3] 在 `scripts/audit_cardhobby_valuation.py` 执行当前逻辑和改进逻辑的相同冻结证据/缓存/预算A/B，输出 `work/audits/valuation-design-r3/metrics.json`；以固定60张及D/U分母报告分层覆盖、错卡、成本/P95和所有卡差异，不发布快照。（FR-010/011/012，SC-003/005/006）
- [ ] T014 [US3] 在 `apps/api/tests/test_valuation_reference_metrics.py` 固定区间距离、unknown排除、1元一次出价、重复商品/不同ID同实卡、运费不计价与近期历史分层；在 `scripts/audit_cardhobby_valuation.py` 生成解盲前参考标签和指标，至少20张充分参考才允许评估总体价格目标。（FR-007/008/011，SC-004）
- [ ] T015 [US3] 仅当T014充分参考门槛满足，在 `apps/api/src/card_platform/domain/valuation_quality.py` 用开发集比较现加权中位数与最小参数调整；先在 `apps/api/tests/test_valuation_quality.py` 固定真实稀疏/跨度/品相回归，再一次性评估20张保留集，记录到 `work/audits/valuation-design-r4/`，不得按保留结果反复调参。（FR-008/009/010/011，SC-003/004/006）

## Phase 6 — Review & Release Decision

- [x] T016 校验 `specs/002-cardhobby-valuation-quality/{spec.md,plan.md,tasks.md}` 的需求映射、样本数量与JSON一致性；运行现有相关纯单测并写 `verification.md`；检查原文件哈希保留。（FR-010/012）
- [ ] T017 在 `specs/002-cardhobby-valuation-quality/verification.md` 汇总R1–R4退出条件、未判定卡、预算、逐条来源、保留集结果及生产回退方案，保存前后对比及回退清单；全部已确认验收标准达到后直接批量写回，未达标不得调用生产重估/回填/队列接口。（FR-012，SC-007）

## Dependencies / Implementation Strategy

T001→T002→T003；T004→T005→T006构成R1可观测基础。T007已提供诊断集，T008补参考；T009/T010/T011在R1完成后做单根因修复。T012依赖T006/T009/T010/T011；T013依赖T012与样本复核；T014可以先完成指标工具，但参考不足不允许T015；T015→T017。T016为本轮文件审查，不代替未来验收。

R1为最小可交付改进：可解释/可回放基线，不承诺新增生产价格。R2每个修复先复现真实症状→最小改动→失败样本及正常对照。未勾选表示仍有未完成子项，已实施部分及证据在verification.md列明。

潜在并行（不默认启动代理）：US1费用trace与人工采样可并行；US2审原图和取得候选详情可独立进行，但同款结论需合并后作出；US3指标工具可与响应夹具整理并行。修改shared worker/gate文件需串行，避免覆盖既有未提交代码。没有标记[P]的源码任务默认串行。


## Phase 7: Convergence

2026-09-21：基于当前代码、spec/plan/tasks检查，8项partial（7 HIGH、1 MEDIUM），无新增计划。以下是既有任务未满足部分的细化，不替换或重编号T001–T017。最终验证与证据见verification.md；当前禁止生产写回。

- [ ] T018 [HIGH] 补齐 `scripts/audit_cardhobby_valuation.py` 与 `image_service.py`/`vision_routing.py`/`valuation_worker.py` 的完整识别路由、有效身份和已发布知识输入一致性；先以真实原图字段角色误读建立失败回归，保存side/region/text-role证据，不把模型猜测、候选身份或confirmed投影混成同一来源；保持原身份不写入。对应F1，FR-003、US2/AC1、T004/T008、plan:R1/R2（partial）。
- [ ] T019 [HIGH] 在 `valuation_trace.py`、`scripts/audit_cardhobby_valuation.py` 和七阶段调用边界对齐 `data-model.md` 的阶段/status契约；补每阶段时长、总等待/执行分离、重试/缓存计数或逐项不可得原因，价格版本与usage账本可核对；测试没有执行的阶段不能伪造成功或零成本。对应F2，FR-004/005、SC-006、T005/T006（partial）。
- [ ] T020 [HIGH] 在 `ingestion/cardhobby.py` 与 `market_data/cardhobby_provider.py` 保存可用卡淘详情/正背图/出价结束字段、内容版本和缺失状态；用 `test_cardhobby_detail_evidence.py` 及 `test_valuation_reference_metrics.py` 验证模板免责声明/具体损伤、运费、付款未知、跨商品ID重复实卡和近期/历史分层，不能仅凭ID去重宣称独立成交。对应F3，FR-007/008/009、US2/AC2/AC3、T010/T014（partial）。
- [ ] T021 [HIGH] 在 `comparable_image_verifier.py`、`valuation_worker.py` 和只读审计补新增采用来源的图片/身份/来源版本闭环；为超过视觉上限、下载失败和整批超时保留明确状态及可复核证据，验证旧判定不能迁移到新图，避免只通过剔除未知来源宣称覆盖改善；逐条确认所有新增采用来源符合SC-005。对应F4，FR-006/009、SC-005、T009/T013（partial）。
- [ ] T022 [HIGH] 在 `ingestion/cardhobby.py`、`market_data/cardhobby_provider.py`、`valuation_worker.py` 协调分页、候选截断与小批视觉验证；先复现前批全部被最终门禁拒绝、后页才有同款的失败测试，采用最终合格且去重证据数驱动停止并记录页/词/图像预算耗尽，不能以标题高分或搜索条数代替。对应F5，FR-004/009/010、SC-003/006、T012（partial）。
- [ ] T023 [HIGH] 对 `sample-set.json` 固定集补充分层商品详情与全部采用来源的独立盲审，记录到 `sample-reviews.json` 新版本及私有证据目录；满足3商品/2卖家/90天且排除可见重复实卡后才建立充分参考，D不足20继续报告数量与unknown；补正常对照、稀疏/极端和同实卡重复的指标测试后才进入价格校准。对应F6，FR-006/008/011、SC-002/003/004、T008/T014/T015（partial）。
- [ ] T024 [HIGH] 在 `scripts/audit_cardhobby_valuation.py` 运行同证据、同缓存、同预算的完整前后链路并保留全部差异与六项指标；固定60张继续回归，另封存按实卡分组且未参与调试的新保留样本，对最终冻结版本一次性评估；不能把已消费20张的缓存重算称新盲测，也不能将不同页数运行作为性能改善证据。对应F7，FR-010/011、SC-003/004/006、T013/T015、plan:R3/R4（partial）。
- [ ] T025 [MEDIUM] 完成 `verification.md` 的机器可核对发布资格、按账户/卡片/身份版本限定的前后对比及恢复清单；先验证无写入dry-run和全部SC通过条件，任何unknown/未达标均阻止发布，只有全部已确认验收通过后按已有授权直接批量写回并验证结果，不重复索要逐步许可。对应F8，FR-012、SC-007、T017、plan:R4（partial）。
