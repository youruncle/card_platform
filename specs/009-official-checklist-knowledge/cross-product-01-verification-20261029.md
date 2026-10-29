# 首批跨产品复用：2024–25 Inception已发布验证

2026-10-29 UTC。用户授权继续验证并直接补入满足同标准的合格批次，不再把普通逐批发布批准作为阻塞。本轮换到2024起始年份、不同产品与来源结构，已闭环至云端发布、实际检索、全快照保护及生产幂等。没有扩大到全网采集、证据上传、新凭据、push/merge、应用部署或原工作区文件修改。

## 实际成果与边界

**v26 / revision1859**，版本ID `28346a44-4202-4d50-b0a9-edd88f6e30ba`，checksum **`82bdd53f184af540ffe6251fadaef4e2752a34cb69ff1e5b497ec305aef06e81`**。

仅更新`topps-8962898588334591`，2024–25 Topps Inception Basketball、Hobby、`BASE CARDS`段。追加7条来源事实及同7规则的查询选项；限编保持**独立资料站声明**等级，未升级为厂家确认：

| 规则 | 限编（编辑站原值） | 官方Hobby列赔率 | 独立审核 / 线上目标候选 |
|---|---:|---|---|
| Purple | 150 | 1:3 | accepted / 1 |
| Pink | 99 | 1:5 | accepted / 1 |
| Red | 75 | 1:6 | accepted / 1 |
| Holo Gold | 50 | 1:8 | accepted / 1 |
| Blue | 25 | 1:16 | accepted / 1 |
| Camo | 10 | 1:40 | accepted / 1 |
| Inception | 1 | 1:395 | accepted / 1 |

Green虽有名称/赔率，但没有明确限编，保留未知并排除，不能默认unnumbered。没有真实来源冲突或待审遗留，没有签字/球衣/Launch扩展、单卡成员展开、card_id/variant_id/physical_card_id、新媒体或向量。其余30,226个实体、关系、官方附件、旧主张和媒体/索引元数据保持。原Bowman13条规则也作为受保护旧知识保持，不是本批重新计数。

这项增量试验累计新增20条规则、涉及2个产品（Bowman13、Inception7）；不是全库规则总数，更不是20张实卡或逐卡覆盖率。

## 来源、真实缺口和独立审核

新只读导出的真实`knowledge_version.snapshot`为v25/rev1857；checksum重算匹配。检查全快照中同产品/2024范围的parallel_options及目标391行官方附件，未发现这些已编码规则；391行的parallel/serial字段均为空，原产品只有文档标题一条补充主张。所以这7条不是把已有卡片版本换个表示后称新增。

本机已有原文与出处得到复用：

- [Checklist Insider原页](https://www.checklistinsider.com/2024-25-topps-inception-basketball#base-checklist)，2026-10-22T01:59:05 UTC实际HTTP200捕获，HTML SHA256 `2018fce08a7072440d38f65192d9ff3115aaa396baa73dd068ee7a733e26d7da`。它直接给限编，本批authority为`independent_checklist_reference`，serial_attribution为`editorial_stated_not_manufacturer_confirmed`。
- 官方清单原PDF SHA256 `38d2f510eb0b8b6591708aa61f7cb07a39abcc897481e07416f6b16b2aafa255`；官方附件SHA256 `b589134eefdd504bb22cb3718d9811d92c934f2cf2b2a37f322b7b0906196db7`。原件第1–2页、附件BASE CARDS段和资料站100个卡号集合一致。没有导入姓名拼写修订或凭产品年份推断赛季。
- 本批只补取一份缺少的[官方赔率PDF](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2024_25_Topps_Inception_Basketball_Odds.pdf?v=1787947694)，来自[厂家Hobby页](https://www.topps.com/products/2024-25-topps-inception-basketball-hobby-box)实际Download odds链接。2026-10-29T16:50:14 UTC HTTP200、175,168字节，SHA256 `c80bdc6206f11f56d9b2a4b1bef770f4d15c831a69d947ff53f5007e60b84ba4`。一页的5918-NBA-INCEPTION 2024-25 HOBBY DISTRIBUTOR列逐条支持名称、基础卡范围和比例，**不直接印限编，未从赔率反推印量**。

与Bowman不同，本批没有厂家教育表直接声明限编；独立审核认可的是有清晰来源等级的可靠补充事实。厂家与资料站分别承担不同字段的证据，不伪造两份独立限编证明或实物核验。

一个独立审核者集中检查7条，使用限定任务上下文而非再按每条分派，217.826秒，7 accepted、0 rejected、0 review_needed；主任务准备与审核并行，不重复加到墙钟。审核原件hash、目视赔率列、官方PDF/附件/编辑站100号集合、已有规则去重和字段级来源均保存于`independent-review.json`。

继承本产品2026-10-22实际厂家/资料站/实卡检索的原始捕获与处置，明确标为历史路线复用。新尝试的Ripped页HTTP403、Blowout读取不可用，均停止、不绕过、不作为事实支持。未使用搜索返回的2025–26相同颜色规则替代2024–25证据。实物图和配对缺口并未因本批规则完成而消失。

## 核心模块复用与完整验收

复用现有`compile_source`、official_document_v1、specialist_facts_v1及`plan_knowledge_intake.py`。新旧产品共用同一发布器、门禁、事务和快照保护；没有改业务工具代码、通用解析器或生产配置。产品选择和数据配方仍需人工准备，不能称全流程无人自动化。

- 双跑2.133 / 1.513秒：parser_calls **1→0**，cache_hits **0→2**，模型API调用0；输入hash未改、输出逐文件hash相同。
- 实际3911条重复字段断言、7条新规则待审。官方391卡的semantic_delta为unchanged391/new0/changed0/removed0，旧文档标题作为重复对照；没有把重复当新增。
- 7条CLI待审理由为既有粗粒度产品规则分组，未改写计划结果；通过独立事实键、source_fact_audit及逐条fact_publication_map进入现有完整准入，无需先改CLI。
- 24文件封存、格式/基线/完整内容门禁通过；本目标未搜索0、待审核0、可用未发布0。原包两次重放一致；限编改值、错误段、审核pending、漏发接纳事实四类篡改重新封存后仍被拒绝。32项既有门禁回归通过。
- 先执行强制READ ONLY并回滚的云端dry-run，通过后按持续补库授权直接apply，无新增逐批批准等待。原advisory/workspace锁、revision/checksum、范围保护和content_id幂等均实际执行，提交前核目标attrs精确等于manifest及其他30,226实体不变。
- 两次独立只读会话从实际v26及parent v25逐实体比较，确认目标仅追加supplementary_evidence/parallel_options，旧主张、来源公开字段、关系和附件保持；实际attrs规范化hash等于封存manifest。
- 云端安装的ScopedCardCatalog对真实基础卡1/Zaccharie Risacher逐条返回7个相应规则各一次。错年份、错产品、999限编、真实签字卡AC-AD范围均不误召回；未知平行/限编仍未知。属于规则检索验证，不是实际卡图识别或每个球员发行每个平行的证明。
- 相同请求再次执行返回already_published/v26；前后版本ID/revision/checksum/claim IDs/逐项结果及计数相同：version_rows26、batch_rows1、dry_run_batch_rows0、request_publish_audits1。worker实际设置true，无新卡实体/媒体/向量。

本批batch_key `inception7-reviewed-20261029`；content_id `d98b4524607c56f911af0b86530a30582f48ebd152d93379fa8a8c36433d7550`。manifest SHA256 **`470b415d53a8795fa3f3bd16f713d0fe1c6dee2dc0172505d06970fd94bcaca8`**，bundle SHA256 **`d835fafeee1d26ea8ab847fab6e6198835361e270da074ae73df0156b6bab854`**。

## 成本与下一批

从16:46:37开始记录至17:04:47机器复验/清单整理，实际1090.67秒（18.18分钟）；包括资料选择、字段归属判断、数据配方、独立审核、预检、发布、回读。报告整理另计，最终含报告总时长见completion-receipt.json。普通逐批发布批准等待为0。

已测：独立审核217.83秒（并行），封存/完整门禁0.877秒，原包重放及四类负样本0.706秒，云端dry-run16.35秒，发布73.29秒，生产重放13.89秒。新增原件下载1份175,168字节；另有2次公开搜索工具调用/3查询及4次公开阅读操作（包括失败页），没有无差别扫网。

产品选择/来源研究、一次性配方适配与后续人工边际审核未分开计时，填未知，均包含墙钟。脚本自动模型API调用0；主任务及一个独立审核者会话token非零但准确计量不可得，保持null。不能只用1.5秒复跑或零API调用代表全流程成本，也不据此给出货币单价。

**跨产品核心路径复用已通过**：2024 Inception的HTML补充事实+一页官方赔率，与2025 Bowman厂家教育表在同一输入、缓存、审核、准入、发布和回读机制上成功闭环。验证仍只覆盖2个产品；资料提取、可信度和独立审核不是全自动，不能宣称近三年已完整或全库无人补全已证明。

下一批优先2023–24 Topps Cosmic Chrome基础卡：元数据预选显示653行官方清单、当前无该产品范围规则，宜继续先查真实缺口与已有原件，再做有界审核和同标准发布。它目前仅是元数据预选，不冒充已扫描/已审核的新批次；普通合格发布不再额外停等批准。父任务可据此衔接，不需要新凭据或上传授权。

## 本机交付

目录`/Users/song/Documents/Codex/2026-10-28/task/real-validation/cross-product-01/`：

- `published-rules.csv`、`publication-verification.json`：精确7条线上状态、来源等级和机器最终比较。
- `current-baseline.json`、`baseline-inception-matches.json`、`candidate-inventory.json`、`cli-verification.json`：真实v25、去重、未知项、双跑hash/成本。
- `independent-review.json`、`inception7-closed-batch/`：原件与24封存文件、独立审核、逐字段映射、准确manifest/bundle。
- `admission-result.json`、`replay-negative-results.json`、`offline-query-results.json`、`gate-regression.log`：门禁、篡改负样本和真实回归。
- `dry-run-result.json`、`apply-result.json`、`repeat-result.json`、`before-repeat-probe.json`、`after-repeat-probe.json`：实际云端执行/回读/幂等。
- `cost-through-verification.json`、`completion-receipt.json`：分阶段成本、准确可观测边界和最终耗时。
- `prepare_cli.py`、`seal_batch.py`、`replay_and_negatives.py`、`run_release.py`、`cloud_probe.py`、`verify_publication.py`：本批数据配方及复用入口，未作为应用代码部署。首次构建拒绝覆盖不可变包；重放不会重建证据。

无剩余本批证据、权限或流程阻塞；没有证据文件上传。本地运行知识库未更新，原工作区改动保持。
