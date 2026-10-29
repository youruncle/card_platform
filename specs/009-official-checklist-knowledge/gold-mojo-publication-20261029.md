# Gold Mojo /50 单条云端发布与实际回读

2026-10-29 UTC。用户明确批准发布已验收的唯一规则；限定2025–26 Bowman Basketball、Mega、`BASE CARDS I CHROME VARIATION`（BCV Chrome）范围。没有发布盒配置或其他候选，没有推断每张单卡都印有此平行。证据包按用户要求留在本机，没有上传。

## 实际结果

| 项目 | 结果 |
|---|---|
| 发布前实际冻结版本 | v23 / revision1853 |
| 发布后实际冻结版本 | **v24 / revision1855** |
| 新版本ID | `e7180b59-df10-4692-a4a3-a046d321aad4` |
| 新checksum | `e04fe8b62ad5b0f71993f46f4e4650b269dc2b96aa433675cdbbf5aecf37498d` |
| 更新产品 / 新知识规则 | 1 / 1 |
| 实际追加字段 | supplementary_evidence一条事实、parallel_options一条范围规则 |
| 其他已发布实体保持原值 | **30,226**（总实体30,227不变） |
| 关系 / 官方附件 / 来源表公开内容 / coverage_registry | 全部与发布前一致 |
| 新卡实体 / 新图片 / 新向量 / 退役卡 | 0 / 0 / 0 / 0 |
| 同请求重复发布 | `already_published`，仍v24、同checksum、同revision |
| 重复前后计数 | version_rows24、batch_rows1、dry_run_batch_rows0、request_publish_audits1，均不增加 |
| 本单条工作范围未搜索 / 待审核 / 可用未发布 | 0 / 0 / 0 |
| 当前阻塞 | 无 |

revision增加2来自既有导入和发布步骤，不代表两条新知识。原有来源、25条参考图、媒体/索引元数据保持原值。内容覆盖的未知分母仍未知，不把范围规则等同于新增实卡覆盖。

## 门禁、事务与证据边界

01:53:25 UTC重新只读导出真实`knowledge_version.snapshot`：checksum、目标产品、来源及完整快照均与原验收基线一致。39个封存文件哈希重放通过，原bundle与manifest字节未改动：

- bundle SHA256 `3847fd40c8e3c1663396f7d907fadceb61228b46705c2921322a59a9c21c6a06`。
- manifest文件SHA256 `77ab0a2acc5874e43a0685309a4063d9e3b24db621f587803c94a30fa83a3b53`。
- importer语义manifest_checksum `3a74df28327e92ea39bdf23ca5b769eeea9c043ae1dfa37ca3453c3eea97d2dc`，与文件字节SHA口径不同。
- content_id `f54355ce81e1996269462430286fe3e488e40caef108c00a518c4ac47ed0758b`；request_id为`annual-content:`加此ID。
- batch_key `gold-mojo-reviewed-20261028`，发布状态`published`。

执行原有、未修改的`scripts/publish_knowledge_release.py`（SHA256 `30feb5e57797c340ba3da33a5d1104b4f3aecaa06f8d3cbf3ec3d9dc36e6e8d1`）。完整封存材料及`validate_intake_audit`仍在本机读取；数据库调用通过已有`card-platform-deploy` SSH的临时标准输入/输出传输，使用运行容器已有Database/Settings连接，不创建或扩展凭证、不开放转发或监听端口、不写远端应用文件。只有知识清单、既有快照的发布参数和必要SQL参数进入数据库；没有传输原始PDF、图片、审核文件或ZIP。

首次连接核验运行容器中的`knowledge_batch`、`knowledge_manifest`、`knowledge_repository`源码哈希与本地一致。传输层在同一事务持有既有发布advisory lock及workspace行锁，再执行原发布器自身锁、checksum/revision校验、原完整内容门禁、导入器和版本发布/审计。只读dry-run使用`SET TRANSACTION READ ONLY`并回滚，未调用会写入dry-run审计行的API入口。

提交前额外比较真实新旧snapshot：实体集合不变，唯一目标产品必须逐字段等于获批manifest，其余实体、关系和官方附件不变，checksum重新计算一致；超范围差异或连接异常均回滚/停止。发布已明确收到事务提交确认，未发生结果不明重试。

新来源URL、内容hash、页码和独立审核hash随事实保存在线；`source_artifact`/独立审核路径指向仍在本机的封存材料，本轮没有声称这些证据文件已复制到云端。线上实际检索使用原有官方清单附件，其运行容器文件SHA另行回读通过。

## 云端实际检索与幂等

发布后的独立SSH只读会话，从实际当前published snapshot构建运行容器已安装的ScopedCardCatalog，使用原有891行Bowman官方附件，而非本地模拟快照：

- BCV-4 Kon Knueppel、Topps/Bowman、2025–26、限编50返回1个Gold Mojo候选，带Mega和批准的Chrome段范围。
- 错年份、限编25、纸卡卡号4、错产品各返回0个本规则候选。
- 未知限编/平行仍保持未知，不由规则自动猜填。
- 这是段级规则的云端检索可见性验证，不是实际卡图识别、单卡发行确认或网页/手机UI实测。

02:00:26 UTC首次独立回读通过；同请求再次调用原发布器返回`already_published`。随后又独立读取版本/批次/审计/事实及规则：版本、revision、checksum、计数和事实值全部一致，没有第二条记录或额外版本。自动成本worker运行设置为true。没有部署、push、merge或修改原工作区文件；本地运行知识库未写入。

## 实测耗时与测试

只读dry-run12.58秒，首次发布（含本机门禁、SSH数据库往返和提交前保护）62.96秒，幂等请求10.47秒。从本轮实际快照捕获至最终机器差分验证至少547.85秒（9.13分钟），包括传输适配、验证和研究性检查；不含最初仓库读取及后续报告整理。前一轮证据准备另有至少38.31分钟，见本机`preparation-cost.json`。这些一次性人工辅助/工程准备成本不能隐藏，也不能据此证明持续全库处理已足够便宜。

脚本没有模型API调用；开发、对话、审核使用的token非零，本环境未提供精确计量。不是全流程零token。

本轮事务保护先写失败测试（2 failed，缺少编解码和差分实现），补齐后2 passed；校验真实UUID/datetime/Decimal/Jsonb传输和四类越界差分拒绝。相关既有annual/manifest回归20 passed。未重跑已知无关15万参数化项；上一轮完整仓库150 failed/22 errors的既有基线未修复，不能声称全仓库全绿。本轮没有修改线上或仓库业务代码、没有放宽内容门禁。

## 本地证据与复现入口

目录：`/Users/song/Documents/Codex/2026-10-28/task/real-validation/gold-mojo-publication/`。

- `publication-verification.json`：最终机器差分、计数、耗时和边界。
- `seal-verification.json`、`dry-run-result.json`、`apply-result.json`、`repeat-result.json`：原包门禁及三次实际执行收据。
- `current-baseline.json`、`published-baseline.json`：发布前后真实云端冻结快照。
- `before-repeat-probe.json`、`after-repeat-probe.json`：独立云端检索与幂等计数回读。
- `apply-transport.json`、`repeat-transport.json`：数据库操作摘要及提交前保护结果，无凭证或原始证据文件内容。
- `publish_via_existing_ssh.py`：临时传输适配，调用原发布器；`cloud_probe.py`：只读运行时复验；`test_publication_guard.py`：本轮保护测试。
- `guard-red.log`、`guard-green.log`、`related-regression.log`：真实测试结果。

实际执行命令（任务目录，复用已存在Python环境；没有为本轮创建新远端访问）：

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=card-platform-incremental/apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 real-validation/gold-mojo-publication/publish_via_existing_ssh.py --mode dry-run
# 同一程序 --mode apply 完成发布；--mode repeat 已验证幂等。
ssh -C -o BatchMode=yes -o ConnectTimeout=15 card-platform-deploy \
 'sudo -n docker exec -i card-platform-cloud-api-1 python -' \
 < real-validation/gold-mojo-publication/cloud_probe.py
```

所有材料及原32MB封存ZIP仍仅留本机；没有另行打包上传，也未扩展下一批。
