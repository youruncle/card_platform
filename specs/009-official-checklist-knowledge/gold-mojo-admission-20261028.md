# Gold Mojo /50 单条完整离线准入闭环

2026-10-28 UTC。本批只含一条新增规则：2025–26 Bowman Basketball，Gold Mojo /50，Mega；只批准既有官方清单段 `BASE CARDS I CHROME VARIATION`。没有发布，没有新增卡片或实物实体。此前盒配置和刚生成的三条本地待审队列均未纳入本批；按最新指示停止扩充。

## 真实证据与独立审核

- 厂家教育PDF第2页直接列 Gold Mojo /50、Mega图标。SHA256 `c6c0a208f627dd2352e6d874ecbda93050de454aa66055041ec51422094df8c6`。
- 从厂家产品页实际链接取得官方赔率PDF，第2页 Base Chrome Gold Mojo 仅在Mega列标1:233。SHA256 `3b67cfcaa2f44afba3b287abbd2b6ce77cb24dd6a13e1806e20caf4dec9a1758`。来源：[Topps赔率原件](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Bowman_Basketball_Odds.pdf?v=1787966369)。不把1:233当成印量，也不把同一厂家两份PDF算成两个独立发布方。
- 独立编辑方[Checklist Insider Mega Box页面](https://www.checklistinsider.com/2025-26-bowman-mega-box-basketball)实际HTTP200，原文 `#base-chrome-mojo-checklist` 同时给Gold Mojo /50、1:233及200个BCV编号，HTML SHA256 `0e5e80c4b03148f1bad00078ea82b57768ae90f6da49fb82d6ee6722c4c1c69d`。它可能使用官方数据，不能当作独立实物核验。
- 官方原始清单PDF与原收据复核通过，SHA256 `1120e7da0c7decf834f3b0a5edaa8bd0c5e0a9882a1e243650c51a311ed35c47`。独立审核实际检查原件第5页版面、第5–8页BCV编号，与已发布891行附件中目标200行及资料站200编号集合逐项相同。
- 实卡路线真实搜索到COMC的BCV-4 Kon Knueppel Gold Mojo /50目录，跟进原页HTTP403；SportsCardsPro替代原页也HTTP403。都保存字节、时间、哈希和失败原因，没有绕过，也没有把搜索摘要当原始实卡图片。厂家产品页直连HTML403，网页读取提取另存并标明类型；可用厂家PDF均实际200。
- 独立Codex审核结论accepted，严格仅段级规则。审核JSON SHA256 `692edb8a114ad9d0c8f5acb70e1bc4353b8fd56656e55a73f32a8d01ea1cbebc`，含目视原件、哈希和编号集合结果。资料站姓名拼写/音标差异及无关Black Mojo冲突均记录为限制，没有导入或修正身份。

因此是真实三路线检索及可追溯处置，不是三份彼此独立的实物证明。厂家免责声明仍适用：列出球员不保证每个球员都有每个平行。本批不展开基础卡×平行，不新增每卡成员表、variant_id、card_id或配对证据。

## 完整离线门禁的实际结果

封存目录：`/Users/song/Documents/Codex/2026-10-28/task/real-validation/gold-mojo-admission/closed-batch`。

实际材料包括已知126正式产品的完整冻结基线（并非年度权威全集）、单产品工作范围、原owning source、三路线请求/结果/原件、全部捕获处置、继承的原清单审核、新事实独立审核、source_fact_audit、fact_publication_map、parallel_rule_audit及39个文件的SHA清单。原25条参考图及原索引元数据保持原值；本批无新媒体/向量，不声称图搜图改善。

未修改现有门禁或降低测试。真实运行 `validate_manifest`、`validate_cloud_baseline` 及 `validate_intake_audit(..., current_snapshot=真实冻结快照)`，结果：

| 项目 | 结果 |
|---|---|
| manifest格式 / 当前checksum与revision绑定 | 通过 |
| 完整离线内容准入 | 通过 |
| 封存39文件的逐项哈希 | 通过 |
| 未搜索 / 待审核（本单条范围） | 0 / 0 |
| 新增结构化事实 / 有界parallel_options | 1 / 1 |
| 新卡实体 / 媒体 / 向量 | 0 / 0 / 0 |
| 数据库连接 / 生产写入 / 线上入库 | 0 / 0 / 否 |

重放相同封存包再次通过。另在临时副本中重新封存篡改文件，现有门禁仍拒绝：限编50改25（与审核值/映射不符）、适用段改纸卡BASE（与独立审核不符）、审核状态改pending（尚未审核）。原封存包未修改。

具体可供用户确认的manifest SHA256：`77ab0a2acc5874e43a0685309a4063d9e3b24db621f587803c94a30fa83a3b53`。bundle.json SHA256：`3847fd40c8e3c1663396f7d907fadceb61228b46705c2921322a59a9c21c6a06`。

## 对识别的实际价值与边界

使用现有ScopedCardCatalog对真实BCV-4 Kon Knueppel清单身份做纯离线前后比较：新增规则前0候选，新增规则后1个Gold Mojo /50候选，段级scope_verification为verified。错年份、错限编25、纸卡#4、错产品四类负样本均不返回该规则；未知限编/平行仍不被enrich自动填写。这是缩小规则候选的验证，不是凭图片成功识别一张真实Gold Mojo，更不证明该球员一定印有此平行。

第一次真实CLI实验仍是1744重复、2候选、1已知冲突，缓存解析1→0、LLM API调用0；本闭环从中只接纳Gold Mojo一条。脚本没有模型API计费调用，但开发、原文解读和独立审核消耗会话token，不能称整项工作零token或完全无人研究自动化。

## 生产边界与最后回读

17:39:36 UTC仅SELECT回读：仍为v23/revision1853、checksum `78e1bf3a8400530c264b3ccc5a1f376160debb1d785ac7db75e1d69ce5a730fc`；对应源有1条active/approved产品记录，保护性名称冲突0，`gold-mojo-reviewed-20261028` 批次行0。未执行会写审计表的API dry_run，没有部署、push、merge或改凭证。

现在没有不可恢复的证据依赖。通过的是现有完整离线内容门禁；生产DB事务dry-run/实际发布与发布后云端回读没有执行。正式写入仍需用户对上述确切单条清单和封存包确认；届时若revision/checksum变化，必须重新核验，不能沿用旧水位。

## 机器可复现证据

在独立工作树执行（复用已有Python环境，网络和DB被校验脚本封堵）：

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 ../real-validation/gold-mojo-admission/verify_closed_batch.py
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 ../real-validation/gold-mojo-admission/check_closed_negatives.py
```

实验目录内：`closed-admission-result.json`（完整门禁及识别回放）、`replay-verification.json`（原包重放）、`closed-negative-results.json`、`independent-review.json`、`cloud-final-readonly.json`、`full-gate.log`。`build_closed_batch.py`可按真实材料重建同一业务批次，不连接数据库；原始输入、CLI记录与132项相关回归见上一阶段记录。所有材料留在本机隔离目录，未另外上传或发布。
