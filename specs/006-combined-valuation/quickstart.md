# 验证与历史重算

从项目根目录运行：

```sh
PYTHONPATH=apps/api/src .venv/bin/pytest apps/api/tests/test_codex_joint_valuation.py -q
TEST_DATABASE_URL=postgresql://.../isolated_test PYTHONPATH=apps/api/src .venv/bin/pytest apps/api/tests/test_codex_joint_postgres.py apps/api/tests/test_valuation_synthesis_postgres.py apps/api/tests/test_portfolio_codex_postgres.py -q
PYTHONPATH=apps/api/src .venv/bin/python scripts/recompute_codex_valuations.py --output .runtime/codex-joint-recompute.json
```

集成库须先执行 db/migrations，不得使用真实卡库写测试数据；各测试事务回滚。重算脚本对真实库使用只读可重复读事务，不改原快照、不调用模型。核验来源及汇率在 apps/api/src/card_platform/data/codex_recovered_sales.json，必须先检查真实来源再更新核验记录。

在 apps/web 运行 npm run build。浏览器搜索 Stitch，确认 ID169/286 的当前估值、原 Codex 报价、联合估值详情及单样本区间；搜索其他冲突卡确认保留原值。
