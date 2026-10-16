# Tasks

## US1
- [x] T001 [US1] 为 apps/api/tests/test_recognition_evidence.py 添加未知、来源与绑定失效的红绿测试。
- [x] T002 [US1] 实现 apps/api/src/card_platform/domain/recognition_evidence.py 并接入识别结果、扫描与采购。
- [x] T003 [US1] 在 apps/api/tests/test_recognition_evidence_postgres.py 验证真实扫描持久化/采购与人工校正保护。

## US2
- [x] T004 [US2] 为 apps/api/tests/test_purchase_cost_subject_identity.py 添加多主体及无姓名具体产品召回测试。
- [x] T005 [US2] 修改 apps/api/src/card_platform/purchase_cost_search.py，有界扩充召回而保留确认门槛。

## US3
- [x] T006 [US3] 在 apps/api/src/card_platform/purchase_cost_service.py 区分非空未匹配与空结果并验证无账本写入。
- [x] T007 [US3] 在 apps/api/tests/fixtures/identity-evidence-regressions.json 与 test_identity_evidence_regressions.py 固定四例证据和对照。
- [x] T008 在 specs/017-identity-evidence-recall/verification.md 记录相关回归、独立审查、实际限制与后续工作。

依赖：T001→T002→T003；T004→T005；T006、T007 依赖契约与召回；T008 最后。单会话顺序执行。
