# 第二批部署 — 026

2026-10-19 03:45（Asia/Shanghai）。授权范围内代码与API已发布；网页继续使用相同已验证资产，无客户端代码变化，不做空重启。没有创建第三批、批量重估、生产身份/价格/知识写回或数据库迁移。

- 实现提交：`fe1c09f669d1e2db18863e3482d34e4e202dca4c`，已推送`codex/valuation-audit-remediation`。使用独立Git index构造23个明确任务文件的提交；主工作树/暂存区及并行成果保留。提交独立导出后157 passed、3数据库条件skip；独立数据库另跑17 passed。
- 实际API：`card-platform-api:0.6.2-20261019-valuation4`，image ID `sha256:d74657e01d6d6aa1f8aac82c3739d150c6b6f182b9ba616e98b851b26fc7c169`。基于切换前实际valuation3镜像，仅叠加7个任务API源文件，构建/切换均核基线，未从本地旧HEAD重建全平台。
- 运行合同：`same-card-source-revalidated-v21`、`paired-image-only-evidence-v7`、`number-roles-2`；联合规则仍`codex-joint-evidence-3`。运行模块实际读取并验证，文字无当前图证准入与缺少marker两个smoke均关闭。
- 运行源代码183个Python文件逐个SHA核验：7个本批文件与发布清单相同，其余176个与发布前生产完全一致，保留并行成本与正背修复。`/health`返回ok、`/health/ready`返回ready、Docker healthy。
- 成本worker：Compose显式`PURCHASE_COST_WORKER_ENABLED=true`；新运行容器环境值`true`，`get_settings().purchase_cost_worker_enabled is True`。启动/回滚脚本都保留开启，仅回滚API镜像，不恢复旧关闭配置。
- 实际网页：`card-platform-web:0.6.2-20261019-valuation2`保持相同镜像和资产。公网首页、价格页、客户端能力接口均HTTP200；分别12/11个静态资源引用，去重14个均200。正背与flatbed能力仍开启。API切换窗口首次能力请求500，API健康后完整公网复检通过；未把该首次失败隐藏为成功。
- 发布前后重复READ ONLY事务核本批200张：身份、持有状态、updated_at、图片URL/SHA均完全一致，400面SHA核验；877张活动名单无变化。事务均rollback。没有手工生产业务写入或批量任务。
- 发布前完整数据库备份336,716,829字节及原Compose保存在服务器私有备份目录；回滚仅替换镜像，保留worker=true。备份、账户、原图、捕获、密钥不入Git。

## 手机与既有等待链路

无需新客户端包。现有唯一`iphone`自动化/`mobile-followup.json`保留`1.1.10 (14)`、个人签名`YLV27B9G9U`、bundle `cn.cardsp.alpha.local`，记录已经覆盖安装并回读版本，启动仍因锁屏待验证；未宣称手机已完成启动验收。本批未重复安装、未创建第二个等待自动化，也未为手机回滚或重部署云端。等待文件只同步新API版本和本批服务器证据，不替换客户端目标或安装状态。

## 接续

本批发布门已通过，可交父协调方判断下一批安排。本聊天不启动第三批，剩余477继续保持未深审。11卡身份、缺失候选背图、模型正常样本uncertain、794标题平行冲突、32项既有相关测试失败和未运行完整E2E等限制见verification-batch02.md；它们没有被发布成功掩盖。

所有私有执行证据在`.runtime/releases/20261019-valuation-batch02/`：source-sha256.json、all-source-verification.json、cloud-activation.txt、runtime-final-sources-health.json、public-verification.json、production-drift-verification.json等。
