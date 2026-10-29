# 12条Mojo规则集中审核与完整只读准入

后续状态：用户确认本批后，已完成v25/revision1857云端发布、12条实际检索及生产同请求幂等；见[发布报告](mojo12-publication-20261029.md)。下文保留发布前准入与成本记录。

2026-10-29 UTC。本轮仅完成候选事实审核、封存、现有完整准入及只读云端dry-run，没有生产发布。**12条全部通过内容准入；发布仍待用户确认这份精确清单。** 没有扩大采集、上传证据、push、merge、部署或修改原工作区。沿用既有隔离分支和发布器，未改业务代码或CLI门禁。

## 可供用户确认的精确清单

全部限定 **Topps / Bowman Basketball / 2025–26 / Mega / `BASE CARDS I CHROME VARIATION`（BCV Chrome）**。限编来自厂家教育页；赔率来自官方PDF的Mega列，只保存为来源核验，不从概率反推印量。

| 规则 | 限编 | 官方赔率页 | Mega列原始比例 | 独立事实审核 / 完整准入 |
|---|---:|---:|---|---|
| Fuchsia Mojo | 299 | 8 | 1:39 | accepted / 通过 |
| Burgundy Mojo | 275 | 8 | 1:43 | accepted / 通过 |
| Purple Mojo | 250 | 8 | 1:47 | accepted / 通过 |
| Pink Mojo | 199 | 8 | 1:59 | accepted / 通过 |
| Navy Mojo | 175 | 8 | 1:67 | accepted / 通过 |
| Blue Mojo | 150 | 2 | 1:78 | accepted / 通过 |
| Aqua Mojo | 125 | 8 | 1:94 | accepted / 通过 |
| Green Mojo | 99 | 2 | 1:118 | accepted / 通过 |
| Yellow Mojo | 75 | 2 | 1:156 | accepted / 通过 |
| Orange Mojo | 25 | 2 | 1:466 | accepted / 通过 |
| Red Mojo | 5 | 2 | 1:2330 | accepted / 通过 |
| Rose Gold Mojo | 1 | 8 | 1:11650 | accepted / 通过 |

Gold Mojo /50已在v24中，作为重复排除。Black Mojo的厂家/1与编辑站/10冲突继续排除，不能用官方赔率推算消解，也不能因Rose Gold /1获准就断言看到/1实卡时只有一种版本。低身份价值盒配置仍未纳入。

本批新增范围仅为12条结构化主张及12个段级parallel_options。没有卡号×平行展开、成员表、card_id/variant_id/physical_card_id、新图片或向量；没有保证每名球员都存在每个平行。原Gold、原始清单、25参考图、旧主张及媒体/索引元数据保持原值。

## 一次独立审核及直接依据

一个独立审核者`/root/review_mojo_batch12`集中完成整批，没有重复分派。逐条实际看厂家教育页、Mega图标及图例、官方赔率适用列、原清单段与编辑站原始HTML，记录每条原文、定位、hash及判断。

关键发现：之前仅查看赔率第2页，会错误认为7条缺行；实际同一官方PDF第8页完整列出这7条的Base Chrome Mojo行。12条全部有明确的Base Chrome/Mega列赔率且与编辑站一致，其他SKU列为空；没有把Chrome Prospect、签字或Mega Rookies/Prospects的行挪作BCV证据。

- [厂家教育PDF](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Bowman_Basketball_Education_Sheet.pdf?v=1776870684)：第2页对应名称与限编、Mega图标；第1页图例。SHA256 `c6c0a208f627dd2352e6d874ecbda93050de454aa66055041ec51422094df8c6`。
- [官方赔率PDF](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Bowman_Basketball_Odds.pdf?v=1787966369)：实际检查全部8页，目标行位于第2/8页Mega列。SHA256 `3b67cfcaa2f44afba3b287abbd2b6ce77cb24dd6a13e1806e20caf4dec9a1758`。
- [Checklist Insider Mega清单](https://www.checklistinsider.com/2025-26-bowman-mega-box-basketball#base-chrome-mojo-checklist)：目标段逐条名称、限编和赔率一致，Black例外已排除。SHA256 `0e5e80c4b03148f1bad00078ea82b57768ae90f6da49fb82d6ee6722c4c1c69d`。
- 原官方清单PDF SHA256 `1120e7da0c7decf834f3b0a5edaa8bd0c5e0a9882a1e243650c51a311ed35c47`；已发布891行附件SHA256 `2d99a034b5af958621b893a2610d8de2d6c218523d9082670c7bd2e6b7aace8f`。原清单第5–8页、附件目标段和编辑站的200个BCV编号集合全部相等，段名原件另有目视核查。

教育、赔率和清单是同一厂家发布方；编辑站可能复用厂家内容，不算独立实物证明。厂家和编辑站原始范围用语分别保存，跨源段映射有独立审核，没有自动吞并来源。正反面、实卡版本及配对仍无新增证据。

定稿独立审核`independent-review.json` SHA256 **`ba3ecee0dcfc919875951186f3ddccc0b598a6c650153cc06535784a6cc5a3e1`**。共12 accepted、0 rejected、0 review_needed；另有Gold重复及Black冲突两个排除对照。原件、原始获取时间、失败响应及前一轮产品级三路线搜索材料按2026-10-28实际记录复用，明确标为继承资料，不声称本轮重新检索或逐规则取得实物图。没有新资料网络请求。

## 完整准入、封存与重复预检

本轮10:48:00 UTC重新只读核对实际云端仍为v24/rev1855、同ID/checksum；复用已实际导出且hash验证的完整snapshot。基线包含126个已有官方附件的正式产品，不把它当年度完整全集；本批工作范围仅1个产品。

复用原流程构建新的`mojo12-closed-batch`，没有覆盖Gold原封存包。44文件逐项hash通过，包含当前完整产品基线、目标范围、独立审核、source_fact_audit（14条逐项处置）、12条fact_publication_map、parallel_rule_audit、原件及继承搜索/失败证据。

真实执行原`validate_manifest`、`validate_cloud_baseline`与`validate_intake_audit(..., current_snapshot=真实v24快照)`全部通过：本目标未搜索0、待审核0、新媒体0、外部缺口单元0。外部缺口单元0仅是本批规则准入统计，不能据此宣称不存在实卡图/字段覆盖限制。

原包不重建连续重放两次，结果相同且所有文件hash不变。四个临时副本篡改后重新封存相应hash，现有门禁仍拒绝：限编改值、适用段换纸卡、事实审核留pending、漏发已接纳事实。原封存包未修改。32项既有知识录入门禁回归通过；没有重跑已知无关15万参数化全库项，也未声称修复原有全仓库失败基线。

CLI原先27条`coarse_product_claim_requires_review`结果保持原样。按可验证的规则身份键和独立审核作出处置，沿用既有事实映射进入完整准入，未被CLI噪音阻断；**本批不需要先改CLI或放宽门禁。**

使用前一轮已有SSH数据库适配，调用原、未修改的`scripts/publish_knowledge_release.py`两次`apply=False`：数据库事务强制READ ONLY，均返回dry_run并回滚。两次content_id和结果一致；清单1、官方附件替换0、退役卡0、新图片/向量0、完整内容审核通过。

- batch_key：`mojo12-reviewed-20261029`。
- batch_id：`knowledge-mojo12-reviewed-20261029`。
- content_id：`46640d40521883af386cb939d37d93e9a2a6268e35a3c9cedd5a1295172385de`。
- **manifest文件SHA256：`5a327c07fbc5241afacdda50cc6694ac1cd0296de762df65aba4049e3fdb93f2`**。
- **bundle SHA256：`b9c2131a0cb68ca57f19975cbffafdc1a3bb84c427d28ae7ff6aabcb6c802f63`**。
- importer语义manifest_checksum：`8179ddb3dc9e896e9116837bebf0d2ab459a27e7ca5dfb9868eb54f501f6f22c`，与文件字节SHA口径不同。

只读重复预检和离线重放无副作用；这不等于本批实际写入后的生产幂等已经执行。新批仍未发布，未来正式发布可沿用已在Gold批实测的请求ID/事务保护，发布后还需实际回读与重复请求核验。

10:55:29 UTC最终实际SELECT：仍v24/revision1855、ID `e7180b59-df10-4692-a4a3-a046d321aad4`，checksum `e04fe8b62ad5b0f71993f46f4e4650b269dc2b96aa433675cdbbf5aecf37498d`；总版本24，新reviewed批次与旧candidate批次行均0。生产写入0、证据文件上传0。未创建凭证、远端文件或持久访问。

## 查询价值与成本

实际使用封存manifest的12条已审核选项，纯离线重放真实BCV-4身份：12个对应限编的目标规则候选分别从0变1；错年份、纸卡卡号和错产品不返回；/50仍仅原Gold一次；未知平行/限编不被enrich自动填写。只证明规则候选可辅助区分，不证明某球员实卡一定发行该平行，不证明真实卡图识别准确率提升。缺少独立真实版本标签/近照评测集；Rose Gold /1也不能消除Black冲突。

截至10:56:23的可观测成本：

| 阶段 | 实测 |
|---|---:|
| 上一阶段候选准备、复跑及报告 | 605.39秒 |
| 本阶段审核、封存、预检及机器复验 | 581.03秒 |
| 两阶段测量区间之和 | **1186.42秒（19.77分钟）** |
| 连续墙钟（含两轮交接间隔） | 1296.03秒（21.60分钟） |
| 独立事实审核 | 146.43秒，与主任务准备并行，不能重复加到墙钟 |
| 封存及首次完整离线门禁 | 1.105秒 |
| 原包重放及4类负样本 | 0.842秒 |
| 两次云端只读dry-run | 89.60 / 88.96秒 |

云端预检包含每次约38.65MB返回数据及多次SSH数据库往返，诊断未见数据库锁等待；没有分别计量传输、序列化及往返延迟。本轮速度不能只用约1秒CLI表示。后续报告整理另计，最终总时长见`completion-receipt.json`。

沿用上一条已开发的CLI/发布器/传输适配；本轮业务工具代码改动0，已有原件和采集成本为复用投入。新增的本批数据配方、映射、封存及测试包装准备成本包含在墙钟中，未独立计时，不冒充零研发成本。正式发布尚未执行，其耗时和写入后复验成本未计。本批只研究一个2025起始产品和同一来源家族，不能外推到2023/2024或跨站采集成本。

脚本自动模型API调用0；主任务辅助准备和一次独立模型审核token均非零，精确计量不可得，填null。结论是值得继续这种同来源家族的有界批量审核试验，尚不能宣称全库持续补充已经达到某个货币/token单价。

## 本地交付与下一步

根目录`/Users/song/Documents/Codex/2026-10-28/task/real-validation/bounded-rules-02/admission/`：

- `accepted-rules.csv`：上述12条逐项状态、限编及官方赔率定位。
- `mojo12-closed-batch/release/manifests/bowman-mojo12.json`、`release/bundle.json`：用户可确认的精确不可变清单和44文件映射。
- `admission-result.json`、`replay-negative-results.json`、`accepted-query-results.json`、`gate-regression.log`：完整准入、重放、查询与真实回归。
- `cloud-dryrun-results.json`、两份`cloud-dryrun-*/dry-run-transport.json`、`final-cloud-readonly.json`：实际只读事务、稳定请求和线上未写入证据。
- `cost-through-verification.json`：墙钟、并行审核、机器步骤及不可观测token边界。
- `seal_batch.py`：仅首次构建，拒绝覆盖既有包；`replay_and_negatives.py`及`readonly_cloud_dryrun.py`用于复验。

本机复验命令（任务目录）：

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=card-platform-incremental/apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 real-validation/bounded-rules-02/admission/replay_and_negatives.py
# readonly_cloud_dryrun.py只执行apply=False；正式发布未获本批授权。
```

下一步仅为用户确认上述12条及manifest hash后再安排受控云端发布；届时必须重新核对当前revision/checksum。不存在未解决的本批离线内容阻塞。证据包仍仅留本机，不上传。
