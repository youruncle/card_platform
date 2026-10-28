# 真实冻结快照上的只读增量实验（2026-10-28 UTC）

结论：可以从真实来源形成两条当前结构化快照没有的知识候选，已有事实会去重，已知冲突保留待审；候选能转换为现有 manifest 形状。完整发布审核没有通过，也没有向线上录入。不能把候选、格式有效与线上入库混为一谈。

## 当前发布基线与范围

- 通过用户新授权的既有 `card-platform-deploy` 连接执行只读导出。普通 Docker socket 访问被拒绝后，读取既有 sudo 权限并核对仓库运维文档，再使用现有 `sudo -n docker` 路径成功；没有新增权限、凭证或密钥。
- PostgreSQL 事务为 `REPEATABLE READ READ ONLY`，仅 SELECT，最终 ROLLBACK。直接读取 `knowledge_workspace.published_version_id` 指向的 `knowledge_version.snapshot`，不是重建 `/entities` 活动表。
- 2026-10-28 16:59:47 UTC 的实际冻结基线：v23 / revision1853 / ID `1888f910-0906-4db3-92e7-dc29ba802d59`，checksum `78e1bf3a8400530c264b3ccc5a1f376160debb1d785ac7db75e1d69ce5a730fc`。完整 snapshot 的语义 checksum 实测一致；30227实体、518附件引用。当前水位与旧记录相同，但已重新回读。
- 518 附件全部导出，压缩字节 SHA256 与描述符、解压行数均一致，无缺文件或哈希错误。2145844 是全部历史附件的校验行数，不是去重基础卡总数。392 个旧附件的1443414行缺逐行 `attributes.source_key`，只算字节/行数校验，不能当成逐卡来源绑定已验收；没有改写这些旧数据。
- 后续 CLI 只提供3个目标附件，合计2359行（Bowman891、Updates1299、UConn169），均通过逐行来源绑定。其余附件不反复解析。CLI 的515条 `missing_artifact` 表示本次输入映射故意未提供的非目标引用，不表示云端遗失；全局目录总数仍为null。

### 132 与历史126的口径

同一冻结快照中，按 `manufacturer in {Topps,Panini}`、`sport=basketball`、`season_start in {2023,2024,2025}` 得132个产品类型实体；其中126个有官方清单附件，另6个明确为 `knowledge_scope=annual_source_directory`：每年各一个 Topps NOW 与 Panini Instant 来源索引。source_key无重复。

| 起始年份 | 正式附件产品 | 年度来源索引 | 合计 |
|---|---:|---:|---:|
| 2023 | 50 | 2 | 52 |
| 2024 | 41 | 2 | 43 |
| 2025 | 35 | 2 | 37 |

差异不是本轮补全成果，也不是运动变化、重复来源或归档筛选产生。按原始起始标签保留单年/跨年，不按版权年改年；NBA、NCAA、WNBA、NBL、EuroLeague不被合并为同产品。冻结快照没有实时active/archive字段，本轮不依据活动表归档状态改写分母。年度权威全集仍未知。

## 小批来源、差分与结果

本批3个产品均为篮球且起始年份2025：`topps-4a27d13d99d59b00`（2025–26 Bowman Basketball，非 Bowman University）、`topps-797f4627628a9c29`（2025–26 Chrome Updates）、`panini-api-2025-1507`（2025单年UConn）。

| 类型 | 证据及差异 | 实际结果 |
|---|---|---|
| 新候选1 | Bowman官方教育PDF第1页Hobby格：8张/包、20包/盒、NBA签1＋NIL签1；快照该产品无box_configuration事实 | new_candidate |
| 新候选2 | 同PDF第2页Mojo表：Gold Mojo /50，Mega图标；快照该产品无此parallel_rule事实 | new_candidate |
| 重复对照 | Updates官方PDF的Delight12张/包、1包/盒、2签；与已发布来源和值完全相同 | duplicate |
| 重复对照 | Checklist Insider的Updates基础卡200张；与已发布来源和值完全相同 | duplicate |
| 冲突对照 | 同资料站Breaker Delight写6张/包，与官方12不同；此前录入审计已经记录此分歧 | review_needed，不计新增 |
| 编译器复用 | 真实UConn官方PDF重新compile_source；169条版本行的语义差分全部unchanged | 1742条字段级duplicate |

合计：**1744 duplicate、2 new_candidate、1 review_needed**；3个来源unchanged、1个new。两条新候选是补充结构化产品知识，不代表新发现两张卡。旧设计资料/捕获的存在不等于已保存相应结构化事实，故不声称首次发现该PDF。Gold Mojo规则不展开每张卡的成员关系或实物身份。

对卡片身份知识的价值不同：Hobby盒配置仅补充产品/包装上下文，不能直接证明单卡身份；Gold Mojo /50＋Mega适用范围可供后续审核后的版本规则消歧使用，但当前未生成parallel_options映射、具体卡成员或识别器变更，不能宣称线上识别准确率已提升。

Bowman原文于2026-10-28 17:03:35 UTC实际HTTP200取得，5404208 bytes，SHA256 `c6c0a208f627dd2352e6d874ecbda93050de454aa66055041ec51422094df8c6`；使用PDFium渲染并检查两页版面，避免文字抽取打乱盒型列。来源：[Topps官方教育PDF](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Bowman_Basketball_Education_Sheet.pdf?v=1776870684)。其余对照复用与发布SHA绑定的真实旧捕获，不伪造本轮HTTP时间。UConn原始下载收据与字节一并保留。字段级来源、定位及旧值见实验目录 `field-differences.json`。

UConn保留1项 `section_without_card_rows` 编译告警，没有把它隐藏为完全无异常。Delight为保守的产品级对象冲突，CLI理由是 `coarse_product_claim_requires_review`，没有自动决定12或6哪一方胜出。

## 双跑、成本与资源修复

| 运行 | parser_calls | cache_hits | CLI模型API调用 | 秒 |
|---|---:|---:|---:|---:|
| 冷缓存 | 1 | 0 | 0 | 1.395 |
| 相同输入复跑 | 0 | 4 | 0 | 1.114 |

输入文件哈希不变，plan.json、coverage.json、report.md逐字节一致。CLI没有发出模型API请求，API计费token为0；不把本会话审阅原文所消耗的交互token算成0。覆盖报告仅有2359个已验证版本行、60条既有参考图记录、1条快照声明的双面样本；年度总产品分母、跨源基础卡总数、全卡图片覆盖率与新核实实物配对均保持未知。

真实附件规模暴露首版资源缺陷：即使只处理产品主张，也保留全部附件卡行并展开所有基线卡事实。两项小型回归先失败，再修复为“范围外保留已验证计数；仅索引incoming产品/subject/field”。专项43 passed；10个相关测试文件132 passed；Ruff、compileall及diff检查通过。未重复运行已有150失败/22错误的15万参数化全套。真实小批进程峰值RSS781287424 bytes；未冒险运行旧版全量展开，不能给出伪造的旧版内存基准。

独立限定范围审查未发现Critical/Important/Minor问题。资源边界仍是逐附件完整解压并保留scope卡行；本轮RSS只代表提供3附件的小批，不是518附件输入的实测峰值，也不宣称恒定内存。

## 发布格式与零写入边界

两条新候选转换为现有单产品manifest，保留原owning source、原属性和旧supplementary_evidence，只追加两条pending事实；实体状态in_review，coverage=[]，不生成card_id/实物ID。实际调用现有 `validate_manifest`，冲突为空；`validate_cloud_baseline` 对冻结checksum/revision通过。这只证明格式有效。

网络被封堵条件下运行纯离线 `validate_intake_audit`，因没有完整录入审计而拒绝：`Intake evidence requires a captured path and checksum`。尚缺完整三路线检索审计、独立事实审核、source_fact_audit、fact_publication_map与封存发布审计材料；没有伪造审核来让闸门通过。

没有调用线上API的dry_run：实际代码 `knowledge_batch.apply_manifest` 在dry_run返回前仍INSERT/UPDATE knowledge_import_batch，会产生写入。只调用无DB连接的纯校验函数。2026-10-28 17:15:52 UTC再次SELECT确认仍为相同v23/rev1853/checksum，`validation-only-bowman-20261028` 批次行数为0。没有push、merge、部署、配置修改、生产写入或本地运行知识库更新。

## 可复现实验与证据

完整实验目录：`/Users/song/Documents/Codex/2026-10-28/task/real-validation`。`input/`保留完整原样snapshot、3附件映射、4来源合同、原始捕获和收据；`complete-baseline.json`另存全部附件映射；`artifacts.tar`及export-receipt保留导出收据。暂存材料不纳入Git提交。

```sh
# 从独立工作树执行；换新的cache/output目录可重新观察冷缓存。
PYTHONPATH=apps/api/src /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
  scripts/plan_knowledge_intake.py --input-root ../real-validation/input \
  --baseline baseline.json --scope scope.json --captures captures.json \
  --cache-dir ../real-validation/replay-cache --output-dir ../real-validation/replay-output
# 重复同一命令观察解析0；输入与输出哈希应保持一致。
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=apps/api/src \
  /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python ../real-validation/offline_preflight.py
```

导出与重建脚本：export-baseline.sql、export_artifacts.py、prepare_experiment.py。实际双跑命令、时间、计数及输出SHA保存在cli-verification.json；其余证据：integrity-summary.json、scope-reconciliation.json、preflight-result.json、cloud-after.json、resource-red.log、resource-green.log、related-regression.log、run1/2.stderr。无密钥或连接字符串写入报告。

本轮完成的是实际增量候选与离线格式验证。下一阶段需要补齐上述发布材料，再另行取得生产写入授权，才能验证线上入库与回读。
