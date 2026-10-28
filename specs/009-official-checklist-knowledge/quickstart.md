# Validation
使用项目.venv运行pytest apps/api/tests/test_official_checklists.py apps/api/tests/test_knowledge_card_catalog.py；本地运行同步命令生成进度账本并验证重跑不重复。--apply后查询发布版本card实体数量与586的Roses补全。运行scripts/replay_official_knowledge.py读取持仓并输出报告。知识库管理入口显示账本百分比与错误；失败产品不得计入成功。


## 浏览改造验证（2026-10-22）
API：.venv/bin/python -m pytest apps/api/tests/test_knowledge_browser.py apps/api/tests/test_knowledge_browser_api.py apps/api/tests/test_knowledge.py apps/api/tests/test_knowledge_card_catalog.py
Web：在apps/web运行 node --import tsx --test tests/knowledge-browser.test.cjs tests/official-checklist-progress.test.cjs，再运行npm run typecheck和npm run build。
Mobile：在apps/mobile运行npm test -- tests/knowledge.test.tsx，npm run build。
浏览：从/knowledge打开厂商/赛季目录、中文别名搜索、系列卡牌清单、卡牌详情、缺图提示、已有图片原图与侧别切换；确认/knowledge/manage保留原管理能力。手机通过Bearer获取图片Blob并全屏查看。核对未登录/普通角色拒绝、错误版本/媒体路径及缺失/损坏来源报错。
本机发布资料只读回放：核对总数、跨厂商/年份样本和实际图像字节；不抓取新资料、不更改发布版本。实际证据填入browser-verification.md。


## 离线增量只读计划（首片）

此命令不抓取、不调用模型、不连接 DB、不生成 release bundle，也不发布。需要 Python >=3.12；PDF/XLSX 使用已有 `checklists` extra，缺依赖会明确阻塞而不会退回模型。

准备三个 UTF-8 JSON 文件，路径均相对 `--input-root`：

1. `baseline.json`：`contract=knowledge-readonly-baseline-v1`，`captured_at`、`workspace_revision`（未知 null）、`published:{version,version_id,checksum}`、完整原样 `snapshot:{entities,relations,official_artifacts}`、`artifact_files:[{checksum,path}]`。可带原始 `sources` 和 `coverage_registry:{status,rows}`。published checksum 必须等于现有 `knowledge.snapshot_checksum(snapshot)`；不是文件 SHA。
2. `scope.json`：`contract=knowledge-intake-plan-scope-v1`、`season_starts:[2023,2024,2025]`、`existing_product_keys:[...]`、`annual_research_keys:[...]`。每个产品 key 唯一且绑定快照里完整的 Topps/Panini 产品属性，不能凭名字猜年度/联盟。
3. `captures.json`：`contract=knowledge-source-inputs-v1`、`items:[...]`。每项有 `input_id,product_source_key,adapter`。`official_document_v1` 携带既有 `product` 和本地 `receipt`（包含 path/sha256/bytes/http_status/status/captured_at）；`specialist_facts_v1` 携带 `facts_path` 指向既有 products/evidence/facts research JSON。每条事实需 field/value 和原文 locator；JSON pointer 不是原文证据。

输入、缓存、输出三个目录必须互不包含。禁止路径逃逸及写目录 symlink。输入保留原样。首次运行只在缓存未命中时确定性解析；重复成功提取不再解析。异常留人工待审。

```sh
PYTHONPATH=apps/api/src python scripts/plan_knowledge_intake.py \
  --input-root /tmp/knowledge-plan-input --baseline baseline.json \
  --scope scope.json --captures captures.json \
  --cache-dir /tmp/knowledge-plan-cache --output-dir /tmp/knowledge-plan-output
```

输出 `plan.json`、`coverage.json`、`report.md`。stdout 仅结果路径；stderr 是 cache/parse/llm 诊断。退出 0 仅表示报告生成，可含 blocked/review_needed/未知统计；退出 2 表示致命错误。已有目录只有包含本 CLI 标记且没有额外文件时才可替换。工作成果是“只读计划，不是录入完成”；focused-v2 发布闸门仍需完整材料。

安全边界：官方 compiler 只接受与已冻结 basketball sport 等价的大小写表达；不把其他 sport/联盟名猜成篮球。嵌套合同类型错误返回 2，坏 gzip artifact 留显式 issue。若替换输出与恢复旧目录同时失败，旧完整结果保留在 stderr 所列 `.knowledge-old-*` 目录，不删除唯一副本；由操作者恢复后重跑。
