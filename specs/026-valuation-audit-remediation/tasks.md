# Tasks — 026

- [x] T001 保存基线并完整读审核主报告，核对恢复状态和生产版本。
- [x] T002 [US1] 在apps/api/tests/test_audit_identity_roles.py复现占位、编号和非体育查询；修共享角色规则与入口。
- [x] T003 [US1] 在apps/api/tests/test_audit_sale_admission.py验证产品/颜色/纹理/赛季/lot及正常对照。
- [x] T004 [US2] 在apps/api/tests/test_audit_sale_independence.py复现实卡去重、条款图反例、本件历史及置信限制；接入valuation_worker.py。
- [x] T005 [US2] 修valuation_worker.py诊断及历史Codex状态，保留原错误和0query缓存语义。
- [x] T006 [US3] 添加Codex实际研究准备/校验入口与严格商品合同，阻止旧经验价导入/提升。
- [x] T007 运行真实样本与正常对照回放、相关及完整API验证，记录未跑和失败。
- [x] T008 发布代码、网页/API，核版本/健康/worker true；手机签名包构建完成。
- [x] T009 更新verification.md及deployment.md，列明剩余问题；不启动剩余677张审核。

- [ ] T010 手机通道恢复后覆盖安装1.1.10 (14)，回读版本并启动验证；当前CoreDevice连接超时/peer不可达，两次安装失败。
