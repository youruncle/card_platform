# 第三批发布 — 026

2026-10-19 05:18（Asia/Shanghai）。本批角色/查询最小修复已发布，正式同卡门v21、视觉v7和联合规则3保留。未启动第四批、批量重估、身份/图/知识/价格写回或数据库迁移。

- 实现提交：`59c047a7892b3638996124581e1fe6904ffa3241`，已推送`codex/valuation-audit-remediation`。独立index构造10个明确任务文件；主HEAD、主index和其他并行成果保留。image_service只提交角色函数差异，不夹带已在线的其他正背识别改动。发布包使用真实线上完整源文件叠加本批差异，保留该既有能力。
- 实际API：`card-platform-api:0.6.2-20261019-valuation5`，image ID `sha256:79edc67e99e3965bc4776960cb9bd18c3a37cde0920dd995ffae7d6b96397e34`；运行revision与实现提交相同。基于切换前真实valuation4镜像构建，只叠加`domain/number_roles.py`、`image_service.py`、`ingestion/cardhobby.py`。
- 构建前及切换前均核183份实际生产源码；发布后183份逐SHA核验，3份任务源与清单相同，其他180份与发布前完全相同。不是从旧HEAD整仓重建。
- 运行合同：`same-card-source-revalidated-v21`、`paired-image-only-evidence-v7`、`number-roles-3`、`codex-joint-evidence-3`。角色/查询smoke、`/health=ok`、`/health/ready=ready`通过，Docker healthy。
- 成本worker：生产Compose显式true，运行容器`PURCHASE_COST_WORKER_ENABLED=true`，`get_settings().purchase_cost_worker_enabled is True`。构建/切换前后均核真值，回滚仅换API镜像并保留worker=true。启动未触发回滚。
- 网页无源变更，继续实际`card-platform-web:0.6.2-20261019-valuation2`，image ID `sha256:a89995837f1e0dc9c0f622464cc4fcc300305041df9dac8231f6e6cb2ab1eede`。公网首页和价格页HTTP200，分别12/11个静态资源引用；去重14个均200，逐资产SHA与发布前相同。能力接口HTTP200，正背确认和flatbed能力仍true。未空重建/重启网页。
- 发布前后READ ONLY/REPEATABLE READ事务重查200张身份、持有、updated_at、图URL/SHA，逐行完全一致；396面SHA及原877活动集合不变，4个缺背继续缺失。查询事务rollback，没有手工业务写入。该检查不宣称手机端完整用户路径或付款/品相认证。
- 发布前完整数据库备份336,716,829字节和原Compose保存在服务器私有目录。备份、账户、库存、捕获、原图、密钥均未提交。第三批6,730个封存文件SHA再校验无变化。

## 验证与未解决项

工作树和独立导出分别173 passed，隔离PostgreSQL17 passed，独立review109 passed并闭环两个P2和一个P3。最终候选363保留案例回放仍全部拒绝、无正式分层变化；该证据与19组真实模型回放分开。

完整API长跑中止，已观察119失败在未修改生产源码逐项重现；不是全量绿灯。新增51项回归不消除已有基线失败。详细失败名单、未跑项及审查前后检查时序见verification-batch03.md。

109个研究身份/绑定未解、4缺背、2受阻来源、864评级无标签图证和200份null独立价格保留。当前模型仍会让正常配对uncertain，261还存在反光误拒；本次不宣称修复该模型问题或提高整体准确率。没有原provider的27个案例继续关闭，没有补造成交、币种、独立副本或图绑定。

## 手机及接续

无新客户端包。复用唯一`iphone`自动化和`.runtime/releases/20261019-cost100/mobile-followup.json`，只同步valuation5服务器证据；保留已覆盖安装并回读的`1.1.10 (14)`、个人签名渠道、安装数据和`installed_awaiting_unlock`状态。启动仍以既有记录待解锁；本批未重新核设备、未重复安装、未创建等待链路，也未称手机全部发布或为手机回滚云端。

本批工程发布门与健康/源码/worker验收已通过，可交父协调方判断下一批安排；本聊天未启动第四批或深审剩余277。下批继续每批≤200，携带上述未解项，先完整审核再统一优化。

私有执行证据：`.runtime/releases/20261019-valuation-batch03/`中的release-verification.json、cloud-build.txt、cloud-activation.txt、runtime-final.txt、production-before/after.json、public-before.json、public-verification.json、preservation.json和mobile-followup-update.json；完整原审核封存目录未覆盖。
