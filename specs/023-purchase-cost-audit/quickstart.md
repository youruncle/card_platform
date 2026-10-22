# 复现

从仓库根目录使用与目标账户相连的配置。不要输出环境配置或会话。首次真实执行以当前已部署云端为准；本地数据库仅用于开发服务验证。

```sh
PYTHONPATH=apps/api/src .venv/bin/python scripts/audit_missing_purchase_cost.py --output .runtime/cost-audit-new/frozen.json
PYTHONPATH=apps/api/src .venv/bin/python scripts/audit_purchase_order_inventory.py --output .runtime/cost-audit-new/inventory.json
# 详情失败可用 --resume 原文件，输出到新文件；不会重做成功详情。
PYTHONPATH=apps/api/src .venv/bin/python scripts/replay_purchase_cost_audit.py --frozen .runtime/cost-audit-new/frozen.json --inventory .runtime/cost-audit-new/inventory.json --output .runtime/cost-audit-new/replay.json
```

账户多于一个时必须明确 --owner。需要实际服务候选时 freeze 工具增加 --candidates，全程数据库 READ ONLY。新 freeze 已保留安全投影及运费政策。

本轮规范冻结文件为 `.runtime/cost-audit-20261017/frozen-fast.json.gz`，历史全知识快照用于投影前后比较：

```sh
PYTHONPATH=apps/api/src .venv/bin/python scripts/replay_purchase_cost_audit.py --frozen .runtime/cost-audit-20261017/frozen-fast.json.gz --inventory .runtime/cost-audit-20261017/inventory-final.json --shipping-policy .runtime/cost-audit-20261017/shipping-policy.json --output .runtime/cost-audit-20261017/replay-new-before.json
PYTHONPATH=apps/api/src .venv/bin/python scripts/replay_purchase_cost_audit.py --frozen .runtime/cost-audit-20261017/frozen-fast.json.gz --inventory .runtime/cost-audit-20261017/inventory-final.json --shipping-policy .runtime/cost-audit-20261017/shipping-policy.json --scope-knowledge --reference .runtime/cost-audit-20261017/replay-new-before.json --output .runtime/cost-audit-20261017/replay-new-after.json
```

输出文件拒绝覆盖和预先存在的 symlink。使用新文件名保存每次回放。

```sh
bash scripts/test-mobile.sh apps/api/tests/test_purchase_cost_diagnostics_postgres.py apps/api/tests/test_purchase_cost_knowledge_postgres.py apps/api/tests/test_purchase_cost_postgres.py apps/api/tests/test_purchase_cost_title_correction_postgres.py apps/api/tests/test_purchase_cost_subject_identity_postgres.py apps/api/tests/test_cardhobby_account_postgres.py apps/api/tests/test_auto_purchase_cost_postgres.py
```

测试脚本使用一次性数据库和文件目录，不读取真实账本进行写测试。不部署、不调用真实 search-result/confirm/sync，不批量回填。
