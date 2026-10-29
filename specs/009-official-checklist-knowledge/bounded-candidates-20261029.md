# 第二轮有界规则候选与成本试验

后续状态：12条已完成一次独立集中审核、44文件封存、完整离线准入及两次云端只读预检，均通过但未发布；见[本批准入报告](mojo12-admission-20261029.md)。下文保留候选阶段的历史状态与成本口径。

2026-10-29 UTC。仅准备候选、只读差分与离线dry-run，没有生产写入或证据上传，没有push、merge或部署。本轮在原隔离工作树进行，业务代码未改动。

结论：已有资料可以一次形成12条有识别价值、两源一致、当前快照缺失的规则候选，值得继续小批验证。当前尚不能证明完整审核发布流程的持续低成本；先解决CLI规则身份过粗造成的待审噪音，再批量独立审核这12条，比扩大采集范围更有价值。**待审候选12，完整准入通过/可直接发布0。**

## 冻结范围与真实分类

10:34:48 UTC通过既有SSH只读核对云端仍为v24/revision1855、ID `e7180b59-df10-4692-a4a3-a046d321aad4`，checksum `e04fe8b62ad5b0f71993f46f4e4650b269dc2b96aa433675cdbbf5aecf37498d`。复用前一轮实际导出的完整冻结snapshot并重新计算checksum，未把活动表冒充快照，也未重新传输37MB内容。

仅检查2025–26 Bowman Basketball这一2025起始年份篮球产品的Mega、BCV Chrome段；没有抽样2023/2024产品，不据此声称覆盖近三年。停止条件为同一已有资料家族的14条编号Mojo规则，不扩展其他平行家族或产品。

| 独立规则身份口径 | 数量 | 处置 |
|---|---:|---|
| 两源一致、目标快照缺失 | 12 | 准备候选，仍待独立事实审核 |
| 已有Gold Mojo /50 | 1 | 重复，排除新增清单 |
| 已知未决Black Mojo | 1 | 厂家/1、编辑站/10，保留各源原值，不发布 |
| 低身份价值盒配置 | 1 | 单独排除，不计入14条规则或新增数 |

28条来源断言来自2个发布方对同14条规则的记录，不是28条新知识。两份厂家材料不能算两个独立发布方；Checklist Insider也可能使用官方数据，不是独立实物证明。本轮没有新资料网络请求，仅只读版本核验；来源捕获时间保留为2026-10-28，未伪装为当天重新获取。

## 精确候选清单

以下12条全部限定 **Topps / Bowman / 2025–26 / Mega / `BASE CARDS I CHROME VARIATION`**。厂家的原始范围用语`Mojo Parallels`与编辑站`Base Chrome Mojo Checklist`分别保存，拟定的段映射有200个BCV编号集合完全一致作为依据。它是待审跨源映射，不是自动把原文改成同一字段。

| 名称 | 来源一致的限编 |
|---|---:|
| Fuchsia Mojo | 299 |
| Burgundy Mojo | 275 |
| Purple Mojo | 250 |
| Pink Mojo | 199 |
| Navy Mojo | 175 |
| Blue Mojo | 150 |
| Aqua Mojo | 125 |
| Green Mojo | 99 |
| Yellow Mojo | 75 |
| Orange Mojo | 25 |
| Red Mojo | 5 |
| Rose Gold Mojo | 1 |

每条候选均保留URL、原始内容hash、获取时间、页码/HTML锚点、原值、目标身份范围和处置理由。没有新增卡号/variant_id/physical_card_id，没有基础卡×平行展开，没有声称每个BCV球员均存在对应实卡。Rose Gold /1与Black Mojo冲突并存，不能凭“/1”就断言唯一实物版本。

复用的直接来源：

- [Topps教育PDF](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Bowman_Basketball_Education_Sheet.pdf?v=1776870684)，第2页Mojo表和Mega图标，SHA256 `c6c0a208f627dd2352e6d874ecbda93050de454aa66055041ec51422094df8c6`。本轮再次实际看图，规则名称、限编和Mega图标可读。
- [Checklist Insider Mega清单](https://www.checklistinsider.com/2025-26-bowman-mega-box-basketball#base-chrome-mojo-checklist)，Base Chrome Mojo表，SHA256 `0e5e80c4b03148f1bad00078ea82b57768ae90f6da49fb82d6ee6722c4c1c69d`。不把其赔率当印量；赔率原文只留证据，没有作为本批新增规则导入。
- 已发布官方清单附件891行，其中目标BCV段200行；附件SHA256 `2d99a034b5af958621b893a2610d8de2d6c218523d9082670c7bd2e6b7aace8f`。200个卡号与编辑站目标段逐项一致。目标快照和附件卡行中未发现12个新规则名称的已有精确记录；未声称完成任意同义名的语义穷举。

## 批量dry-run与查询价值

直接复用现有`plan_knowledge_intake.py`、输入合同、内容hash缓存和纯manifest校验器，没有新工程项目或解析器重构。`prepare_and_run.py`仅是本批数据配方与统计脚本，使用已有适配器；不包含发布路径。

- 同一真实输入双跑：1.113秒、0.990秒。结构化输入cache_hits为0→2；CLI parser_calls为0→0，llm_calls为0→0。PDF转结构化在CLI之前完成，不能把这个0解释成没有准备成本。
- 输入所有字节hash不变，CLI输出逐文件hash相同，12个候选ID唯一，没有因复跑追加重复候选。
- CLI真实状态为27条`review_needed`、1条`duplicate`；27条理由均为`coarse_product_claim_requires_review`。它按“产品+parallel_rule字段”分组，因此不同名称/限编、不同原始范围用语也一起进入待审。12/1/1是保留来源的规则身份对照分析，**没有改写CLI结果或降低门禁**。
- 待审manifest含12条新增主张及12个拟定范围选项，实体/规则保持in_review/pending。原有Gold、其他属性和媒体保持。纯格式校验无冲突；完整独立事实审核、fact_publication_map与本批封存准入未完成，**没有运行生产事务dry-run或完整发布门禁**。格式校验器的publishable字段不能代替内容批准。
- 纯离线假设加入这些待审选项后，使用真实BCV-4清单身份，12种限编各能找到其目标规则；此前这12种范围规则均无候选。错年份、纸卡卡号、错产品不召回；/50仍只返回原有Gold，不误召回12条新规则。未知平行和限编仍不被enrich填入。

这说明规则能辅助区分例如Blue /150与Aqua /125、Green /99与Yellow /75；不是线上检索已经改善，更不是照片识别准确率提升。缺少带真实版本标签的独立正反面/近照样本、冲突实卡证据和前后评测集，无法测实际Recall@K或误识别率。

10:41:12 UTC结束只读检查：仍v24/rev1855、同checksum，`candidate-mojo-12-20261029`线上批次行0。

## 成本与最小改进

从本轮开始记录到候选/差分/查询验证结束实际422.11秒（7.04分钟），包含查看来源、范围核对、一次性数据配方准备及执行；不含之后报告整理。配方实际执行3.765秒，内含两次CLI；单页PDFium提取另测0.0216秒。前期也探索读取过两份PDF，探索读取未独立计时；都包含在总耗时内。资料解读与配方编写交错，无法准确拆分人工研究/审核秒数，记未知。

本轮业务工具研发为0（没有改应用/CLI），一次性数据配方编写成本包含在7.04分钟中，未单独计量。机器复跑约1秒不等于未来每批完整边际成本；下一批人工核验、独立审核和封存的边际成本仍未知。前一轮38.31分钟包含完整准入，本轮7.04分钟仅到候选准备，阶段不同，不能据此声称效率提升倍数。

脚本自动模型调用0；会话辅助准备token非零，精确数量不可观测，填null。没有把人工辅助研究称为零token自动化。

在扩大批次之前建议的最小工具改进（本轮未实施）：对结构化parallel_rule增加保守的规则身份比较：产品、官方名称、明确盒型和已证实段范围；不同规则不产生假冲突，同名同范围限编不一致仍须待审。保留原始范围用语、来源与定位，不能自动吞并来源或自动批准映射。至少验证“不同规则可并存”“Black同名限编冲突仍阻断”“已发布Gold仍去重”三项；范围不能确定时仍退回现有待审行为。

下一步只针对这12条做一轮有界独立审核、完整事实映射/封存和离线门禁，再衡量完整批次边际成本。本轮不扩采、不发布，不把候选数量当完成量。

## 本机复验材料

目录`/Users/song/Documents/Codex/2026-10-28/task/real-validation/bounded-rules-02/`：

- `candidate-list.csv`、`candidate-inventory.json`：14条身份逐源原值/分类，含12条候选。
- `input/`、`output/plan.json`、`summary.json`：真实baseline、原件、2源28断言、CLI输出和双跑hash。
- `pending-manifest.json`：精确12条拟定清单，全部待审；不应直接发布。
- `query-simulation.json`、`baseline-row-dedup.json`：真实清单范围及纯离线查询验证。
- `cost.json`、`current-version.json`、`final-cloud-readonly.json`：成本和线上只读回读。
- `prepare_and_run.py`：可重复数据配方。运行前保留原`summary.json`以免冷缓存统计被温缓存运行替代。

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=card-platform-incremental/apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 real-validation/bounded-rules-02/prepare_and_run.py
```
