# Validation

运行 `bash scripts/test-mobile.sh apps/api/tests/test_auto_purchase_cost_postgres.py apps/api/tests/test_purchase_cost_postgres.py`，使用隔离临时PG，不访问真实卡库。验证新卡排队、唯一自动确认、无结果、账户不可用、事务回滚、去重、已有交易和隔离。上线前先执行0082迁移，再启动既有成本worker。真实卡淘网络和生产部署另验收。
