# 识别字段证据与采购召回 Implementation Plan

> For agentic workers: use superpowers:executing-plans inline. 用户已授权持续推进；不重复设确认门槛。

Goal: 未知字段保持未知，扩大有界召回，并继续严格确认成本。
Architecture: 在现有识别结果中增加服务端字段评估；扫描保存时绑定实际定义及照片摘要。采购读取时仅对绑定仍有效的新扫描结果恢复逻辑未知，不更改真实定义和账本。复用原有搜索、候选核验和入账服务。
Tech Stack: Python/Pydantic, PostgreSQL；无新依赖或迁移。
Spec: specs/017-identity-evidence-recall/spec.md

## Global Constraints
保留工作区已有修改；一份 spec/plan/tasks；不覆盖历史人工身份/成本；不增加模型调用；不把召回当作同款证据。

## Review Focus
- 定义、限编或原图被改后旧字段评估必须失效。
- 目录候选/提供者自报来源不能伪装核实证据。
- 三人卡只发现一个主体仍不能自动入账。
- 有订单但文本未召回仍显示未匹配且无写入。
- 空值/泛产品/过长值不能无限扩大检索。

## Task 1: 字段契约及存储绑定
Files: 新增 domain/recognition_evidence.py；修改 schemas.py、vision_routing.py、image_service.py、card_search_service.py、scanner_queue.py、purchase_cost_service.py；测试 test_recognition_evidence.py、test_recognition_evidence_postgres.py。
接口：assess_recognition_fields(result) 生成字段状态；bind_recognition_storage(result, card) 写不可由提供者继承的绑定；purchase_identity_view(card, result) 验证后投影逻辑值。
先写未知/高置信/目录来源/冲突与更改绑定的失败测试；实现纯函数；集成扫描落库与采购读取。测试命令 .venv/bin/pytest -q apps/api/tests/test_recognition_evidence.py。

## Task 2: 有界召回与结果语义
Files: purchase_cost_search.py、purchase_cost_service.py；测试 test_purchase_cost_subject_identity.py、test_recognition_evidence_postgres.py。
复用 player_roster_components，各主体一条主查询；具体产品+正面图可在无姓名时召回；泛产品禁用。完整非空 orders 视为发现记录。先测试再改动。

## Task 3: 固定真实案例回归和验收
Files: apps/api/tests/fixtures/identity-evidence-regressions.json、test_identity_evidence_regressions.py；specs/017-identity-evidence-recall/verification.md。
保存去隐私字段证据/卖家标题及正确身份；每例分别验字段、召回和同款反例。相关测试+隔离 PostgreSQL，独立审查本轮增量。保留红绿日志与新增文件/基线快照至 .runtime/identity-evidence-optimization。

## Execution decisions
当前工作区已有大量未提交成果，直接在原工作区增量编辑并存本轮前快照；不新建遗漏现有代码的 checkout，不提交混合改动。所有任务依次执行；不做全库数据写入。

## Execution ledger
- Task 1：字段契约红绿首次 8 failed / 10 passed；追加来源伪造、日期和产品猜测回归后最终 28 单测通过。扫描全链路 PG 验证真实默认年份仍兼容存储、采购投影为空、无法据默认年入账、后续身份改动使旧绑定失效。
- Task 2：多主体/无姓名产品召回首次 3 failed / 31 passed；修复后相关 224 项通过。完整非空未召回订单首次 not_found 失败，修改后 unmatched，账本为空。
- Task 3：四例固定证据首次 2 failed / 9 passed，定位 JUN23 连写日期和 NS-XX 猜 Hoops；修复后四例字段/召回、四个错误同款对照、三个正常召回对照全部通过。
- Ruling：catalog_verified_fields 新增为服务端应用目录结果的值绑定标记，防止模型仅靠“目录核验”前缀与官方 URL 冒充已核实；入口清理，网页真实读取且支持候选后才能生成。
- Ruling：现有 scanner 时间测试检查整个数据库所有 idle transaction，组合执行一次失败；单独运行 4 项通过。将其单独验收，保留原组合失败日志；不改写该测试或据此声称完全无状态干扰。
- 相关隔离 PostgreSQL + 全部迁移：501 passed；scanner 时间测试独立：4 passed。

- Final: fixed 审查的历史问题残留、同值人工确认、绑定缺失字段及泛产品边界；各新增失败回归转绿。
- Final: fixed OCR 发行角色例外；提取与最终检查两层覆盖 college basketball season，保留真实发行行正对照。
- Final review: independent reviewer，无剩余 Critical/Important；字段状态与截图入口均检查。

- Final verification: 546 relevant migrated-PostgreSQL/unit regressions passed; scanner timing 4 separately passed; full backend 157523 passed / same 127 failures / 163 skipped. Failures compared as exact name sets: added 0, removed 0. API restarted and API/Web healthy. Existing 873/875/876 ledger snapshots unchanged.
