# 云端实卡采集程序V1

程序入口：`scripts/knowledge_intake_worker.py`。复用现有官方清单、公开商品图库及媒体/向量/发布合同。它自动执行采集与记账，逐图核验仍通过结构化审核文件提交；不会调用大模型、确认持仓、估价或直接发布知识。

## 云端运行

在线API资料目录保持只读。当前已安装一个独立worker代码包与云端PostgreSQL队列；启动器使用当前API镜像，只读挂载正式资料，只把本批目录设为可写。启动器从运行API读取DATABASE_URL，不复制视觉/搜索密钥或整份环境配置，不启动应用worker。

宿主机入口：

```sh
sudo python3 /home/ubuntu/card-platform/runtime-migration-data/knowledge-intake-worker-v1-20261023/launch.py \
  report --batch-id image-intake-v1-20261023-300-r2 --details

sudo python3 /home/ubuntu/card-platform/runtime-migration-data/knowledge-intake-worker-v1-20261023/launch.py \
  run --batch-id image-intake-v1-20261023-300-r2 --max-tasks 10 --max-items 1 --max-images 4
```

每次run有界处理队列；重复运行继续未处理任务，已完成任务不重跑。站点采集持有跨批次互斥锁，请求间隔至少1.5秒；默认最多三次错误重试并退避，403/429搜索阻塞保留状态。进程中断后未完成租约到期可恢复，旧worker不能完成新租约。

新批次在worker容器内运行`init --batch-id <新ID> --data-root <云端本批可写目录> --limit 300 --years 2023 2024 2025`。首次安装迁移时加`--apply-schema`；文件和数据库都以云端为准。不能对本地运行知识库运行此CLI。

## 审核与导出

`report --details`返回候选ID、不可变内容SHA、捕获的图片/来源及冻结目标。核对原图后，将审核JSON放入本批云端目录，运行：

```sh
.../launch.py review --batch-id <批次ID> --review-file <容器内审核JSON路径>
.../launch.py export --batch-id <批次ID>
```

审核文件为对象或对象列表，每项包含`candidate_id`与`review`。审核合同：

| 字段 | 要求 |
|---|---|
| candidate_sha256 | 本次候选的实际内容hash；不能沿用另一捕获或批次 |
| decision / reviewer / reason | accepted或rejected、真实审核者、明确依据 |
| identity | 接受时须与冻结目标一致；无法核实就拒绝或保持待审，不能照抄目标当核验 |
| identity_evidence | 球员、卡号、产品和赛季的实际可见依据；非空insert_name/parallel_name/serial_total分别给出依据；非Base原始section也必须单独核图 |
| serial_total依据 | 限编版本须记录图中实际且唯一的x/y，分子合法、总量与官方版本一致 |
| images | 接受时覆盖每个已捕获图片index与SHA、accepted/rejected、面别和观察；不能凭图库顺序选面别 |

拒绝候选至少填写hash、decision、reviewer、reason；图片损坏或范围错误的理由也应保留。接受单面是合法操作。V1导出一律不主张同实物配对；已有认证或限编号配对仍走原严格合同。

导出文件为本批`export/assets.jsonl`，只含已接受且重新检查字节/身份/来源的ready媒体。可交给`scripts/index_knowledge_vectors.py`及现有知识资料包整合器；同时应保留captures、审核与清单附件。知识封版还须依照`docs/knowledge-intake-sop.md`实际完成三类来源路线、全部候选处置、索引、基线比较和云端发布回读。导出成功不表示本批已封版、已发布或全库补齐。

## 进度含义

任务captured只表示取得候选，no_results只表示执行的有界查询无结果，blocked/error分别保留原始诊断。候选pending_review/accepted/rejected与任务状态独立。报告中的published_images只统计本worker直接发布的数量，本版始终为0；外部发布后需用正式知识版本回读证明，不能据此字段判断外部发布状态。

首版仅接卡淘公开来源，官方清单用于冻结身份。未内置OCR/大模型审核、图站全量爬取、常驻服务、管理界面或自动知识发布。运行实测与剩余范围见`specs/009-official-checklist-knowledge/intake-worker-verification.md`。
