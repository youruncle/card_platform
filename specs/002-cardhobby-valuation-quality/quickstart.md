# 只读复现与验证

在仓库根目录运行。Spec Kit 与 Superpowers 共用本目录；当前是已批准实施，尚未达到发布条件。所有输出使用新的 `work/` 子目录，禁止覆盖冻结证据或正式估值。

## 冻结基线

```sh
PYTHONPATH=apps/api/src .venv/bin/python specs/002-cardhobby-valuation-quality/export_baseline.py --out work/audits/valuation-repeat/frozen
```

已有目录拒绝覆盖；会话默认只读，REPEATABLE READ READ ONLY；多owner停止。现有固定基线为 `work/audits/valuation-design-20260921/frozen-v2/baseline.json`，SHA256为 `d0bb8027e5a2aff032c841d68d5a0e0a672c32f279bfddac292a2ab6ce212285`，519张、3373条卡淘候选。

## 离线分类/定价回放

```sh
PYTHONPATH=apps/api/src .venv/bin/python scripts/audit_cardhobby_valuation.py \
  --baseline work/audits/valuation-design-20260921/frozen-v2/baseline.json \
  --samples specs/002-cardhobby-valuation-quality/sample-set.json \
  --owner-attestation work/audits/valuation-implementation-20260921/owner-attestation.json \
  --out work/audits/valuation-repeat/offline-development \
  --split development
```

离线不联网、不调用模型或worker.run/_calculate。owner-attestation绑定确切冻结文件；重新导出后需重新只读验证owner范围，不能沿用旧证明。身份解析共享worker纯函数，但离线未加载全部已发布知识别名，不宣称完整生产链路等价。

## 只读卡淘检索

在上述命令中增加 `--mode shadow-retrieval --max-pages 12`，使用另一新输出目录。每卡最多12个逻辑页面，每词最多2页，最终最多20条候选；HTTP内部重试与逻辑页分别理解。连续3卡全部查询失败时熔断，剩余显式受阻。原始搜索响应和逐行拒绝写入events。此命令不运行视觉或发布价格。

`visual_shadow_card()`用于新视觉诊断与同款影子验证，实际运行脚本和逐卡结果在 `work/audits/valuation-implementation-20260921/`。该诊断采用新视觉调用、冻结的有效身份及缓存检索，识别结果不会覆盖已确认身份。完整CardVisionService路由另有14张开发样本诊断；不能把单模型诊断称为完整生产端到端A/B。

首次保留集在functional-freeze-v2后运行；已消费的保留集不得重新称为未见样本。后续若基于新诊断继续调参，需另外保留未参与调试的样本。

## 测试

```sh
PYTHONPATH=apps/api/src .venv/bin/pytest -q apps/api/tests/test_valuation_trace.py apps/api/tests/test_valuation_identity_provenance.py apps/api/tests/test_comparable_version_texture.py apps/api/tests/test_cardhobby_recall_budget.py apps/api/tests/test_valuation_reference_metrics.py
CARD_PLATFORM_TEST_PORT=55441 bash scripts/test-e2e.sh
```

完整测试脚本新建临时PostgreSQL、应用全部迁移、运行API全部测试并清理。不要设置生产库为TEST_DATABASE_URL。复用已运行过的测试库会触发“首用户管理员”夹具状态差异，应使用全新隔离库。只读事务mutation拒绝测试需隔离TEST_DATABASE_URL，否则明确skip。

本轮未修改前端，不运行typecheck/build；已有前端修改属于用户起始工作，保留。

## 结果与边界

- `comparison-release-review/`保存最终控制回放、两轮相同单页候选的探索性视觉结果及最终12页检索结果；不能将不同预算/来源的结果当相同条件A/B。
- `controlled-replay-v7/`保存开发集暴露旧视觉观察失效回退后的修复结果；不覆盖任何旧版本结果。
- `development-visual-recalculated-v7/`、`holdout-visual-recalculated-v7/`仅对已捕获视觉结果按最终规则重算，不新增模型调用，原始耗时不是本次完整执行耗时；不得把已消费保留集称为新的盲测。
- `browser-evidence/`保存浏览器取得的商品图、页面事实及独立盲审；付款未确认，不把落槌称付款。
- `sample-reviews.json`覆盖全部60张；详情、背图、可比品相或独立成交不足时reference_interval保持null。
- 发布资格和剩余缺口见verification.md。禁止以有价数量或单测通过代替合理覆盖验收；未达标不运行生产回填/重估/队列接口。
