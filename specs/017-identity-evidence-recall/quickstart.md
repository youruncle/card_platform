# Validation
运行 .venv/bin/pytest -q apps/api/tests/test_recognition_evidence.py apps/api/tests/test_identity_evidence_regressions.py apps/api/tests/test_purchase_cost_subject_identity.py。
使用隔离 PostgreSQL（全部迁移）运行 test_recognition_evidence_postgres.py 与已有采购/扫描相关回归。
预期固定案例、所有权和绑定变化保护通过；完整非空未匹配不写交易。
