# 12条Mojo规则云端发布与实际回读

2026-10-29 UTC，用户明确批准完整准入清单后，仅发布本批12条规则，已完成实际云端回读及同请求生产幂等验证。没有push、merge、部署、证据包上传或原工作区文件修改；本地运行知识库未写入。

## 实际发布结果

| 项目 | 实际结果 |
|---|---|
| 发布前新核验 | 16:28:46 UTC，v24 / revision1855，本批记录0 |
| 发布后 | **v25 / revision1857** |
| 新版本ID | `4956ebf7-2ac7-4007-9159-0e5f313267f4` |
| 新checksum | `84702724d6db830b3641c540613aba229c3cd0d3df2ed25c8f2b5a467f9b9b71` |
| 实际更新 | 1个产品，追加12条来源事实及同12规则的范围查询选项 |
| 其他已发布实体保持原值 | **30,226**，总实体30,227不变 |
| 关系 / 官方附件 / 原Gold / 旧资料和索引 | 保持原值 |
| 新卡实体 / 新媒体 / 新向量 / 退役卡 | 0 / 0 / 0 / 0 |
| 同一请求重复执行 | `already_published`，仍v25、同checksum与revision |
| 重复前后计数 | version_rows25、batch_rows1、dry_run_batch_rows0、request_publish_audits1，均不增加 |
| 当前阻塞 | 无 |

revision增加2来自既有导入与发布步骤，不是两次发布或24条不同知识。每条来源主张与它的查询选项是同一规则的两个用途，不重复计算新增量。

本批已发布且逐条云端运行目录检索通过的范围，全部为 **Topps / 2025–26 Bowman Basketball / Mega / `BASE CARDS I CHROME VARIATION`（BCV Chrome）**：

| 规则 | 限编 | 线上目标候选数 |
|---|---:|---:|
| Fuchsia Mojo | 299 | 1 |
| Burgundy Mojo | 275 | 1 |
| Purple Mojo | 250 | 1 |
| Pink Mojo | 199 | 1 |
| Navy Mojo | 175 | 1 |
| Blue Mojo | 150 | 1 |
| Aqua Mojo | 125 | 1 |
| Green Mojo | 99 | 1 |
| Yellow Mojo | 75 | 1 |
| Orange Mojo | 25 | 1 |
| Red Mojo | 5 | 1 |
| Rose Gold Mojo | 1 | 1 |

原Gold /50保留且仅1条。Black /1与/10来源冲突未导入，没有用Rose Gold /1消解它。没有展开每张单卡都具有这些平行，没有新增成员表或实物身份，也没有声称照片识别准确率或逐卡覆盖提升。

## 发布前与事务保护

重新SELECT云端版本/checksum后确认与封存v24基线完全相同；44文件逐项hash、accepted-rules.csv与manifest的名称/限编/身份集合、原完整内容门禁再次通过。基线未变化，无需重建包或重做采集。仍使用用户确认的不可变材料：

- manifest文件SHA256 `5a327c07fbc5241afacdda50cc6694ac1cd0296de762df65aba4049e3fdb93f2`。
- bundle SHA256 `b9c2131a0cb68ca57f19975cbffafdc1a3bb84c427d28ae7ff6aabcb6c802f63`。
- batch_key `mojo12-reviewed-20261029`，content_id `46640d40521883af386cb939d37d93e9a2a6268e35a3c9cedd5a1295172385de`，request_id为`annual-content:`加该ID。
- 独立审核、字段映射、来源与第2/8页Mega赔率列证据见[完整准入报告](mojo12-admission-20261029.md)，本轮没有改变审核裁决。

调用原、未修改的`scripts/publish_knowledge_release.py`，沿用既有SSH别名和运行容器Database/Settings。原advisory lock、workspace行锁、checksum/revision、数据库产品碰撞、内容ID幂等和受控范围发布均实际执行。提交前额外核真实新旧全snapshot：实体集合相同，目标产品逐字段等于获批manifest，其余30,226实体、关系及官方附件不变，checksum重新计算一致。

收到明确提交成功回执后才回读和执行重复请求，没有因等待较长就再次发起apply，也未出现提交结果不明后的盲重试。原封存文件、PDF、PNG、审核JSON与ZIP始终留本机；只有获批知识清单、必要数据库发布参数及来源定位/hash进入线上，没有创建远端应用文件、凭证或持久访问。

## 独立云端回读和幂等

通过两次独立SSH只读会话，从数据库实际published snapshot及parent v24逐实体比较，不依赖成功响应替代回读。实际目标attributes的规范化hash与获批manifest完全相同，且只变化supplementary_evidence和parallel_options两个字段；旧数组前缀、媒体/索引元数据、目标来源公开字段保持。

在运行容器已安装的ScopedCardCatalog、真实v25快照和原官方清单附件上逐条检索BCV-4对应限编：12条均返回对应规则一次，段范围verified；错年份、纸卡卡号、错产品，以及/50对这12条新规则的负样本均为0。未知限编/平行仍保持未知。原Gold /50不被新规则误替换；附件实际字节hash通过。这是云端运行目录的规则检索，不是网页/手机UI或真实卡图识别实测。

同一内容请求再次调用原发布器，返回already_published/v25。随后再次独立回读：版本ID、revision、checksum、12事实/选项、全部claim IDs、目标attributes hash、逐项查询结果、批次行及发布审计计数完全一致。成本worker实际设置为true，没有配置、镜像、网页或手机改动。

## 实际成本和执行限制

首次发布460.36秒（7.67分钟），同请求幂等执行10.20秒。发布事务期间实际观察到一条workspace FOR SHARE读取等待；没有取消其他业务会话。成本包含完整快照在数据库与本机之间多次往返及提交前保护，不是只计算12条小JSON的耗时。

本机SSH别名默认compression=no、ControlMaster=auto；本次首次apply沿用已有复用连接。后续验证将同一别名明确设为压缩、ControlMaster=no/ControlPath=none的临时连接，避免会话设置受既有master影响；不改变任何凭据、持久访问或线上应用文件。没有分别计量压缩、序列化、网络往返和SQL耗时，不把发布与幂等请求不同步骤的时长比值写成加速倍数。原2项编解码/提交前差分保护回归再次通过。

截至16:40:31完成机器复验：本发布阶段707.76秒；先前候选、一次独立审核、封存、两次只读预检及报告1373.93秒；合计测量执行区间2081.69秒（**34.69分钟**）。之后的报告整理另计，最终含报告总时长见completion-receipt.json。等候用户发布授权的数小时不计入执行区间合计；独立审核并行时间没有重复相加。

本批已有来源和已开发工具得到复用；本批数据配方、映射/封存、包装脚本及临时连接设置调整的准备时间已包含，未精确拆分为一次性与每批人工成本。它不是跨产品/跨年份全库成本评测，不能据此承诺固定金额或持续全自动低价。

脚本自动模型API调用0，主任务和独立审核模型的会话token非零、准确数量不可观测，保持null。本批没有新增数据采集请求、实卡评测或向量编码成本。

## 本机证据和交付

目录`/Users/song/Documents/Codex/2026-10-28/task/real-validation/bounded-rules-02/publication/`：

- `published-rules.csv`：12条实际发布状态、v25版本ID/checksum及查询结果。
- `fresh-seal-verification.json`、`apply-result.json`、`repeat-result.json`：新基线、44文件/CSV、真实提交和幂等回执。
- `before-repeat-probe.json`、`after-repeat-probe.json`：两次独立实际快照及运行目录核验。
- `publication-verification.json`、`final-verification.log`：字段/来源/全实体保护、计数、成本和限制的机器比较。
- `apply-transport.json`、`repeat-transport.json`：请求摘要、提交前保护及明确提交结果；不含凭证或原始证据文件内容。
- `publish_exact_batch.py`、`cloud_probe.py`、`verify_readbacks.py`：复用原发布器的批次绑定、只读回读和本机比较入口。未作为应用代码部署。
- `transport-guard-regression.log`：两项保护回归结果。没有重跑无关全仓库测试，原有全库失败基线不在本轮修复范围。

复验本机已保存的两次实际回读（不连数据库）：

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL PYTHONPATH=card-platform-incremental/apps/api/src \
 /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
 real-validation/bounded-rules-02/publication/verify_readbacks.py
```

本批已到可验证终态，没有剩余发布或回读阻塞；没有自动启动下一批。
