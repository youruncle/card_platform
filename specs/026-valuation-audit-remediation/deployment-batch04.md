# 第四批发布（2026-10-19）

本批源码提交`979af45fbab96231b76030e09f307f387a8daafa`，已推送`codex/valuation-audit-remediation`。只提交5个源码、5个测试及同一规格记录；主HEAD`26573d43`和主index保留。文档后续提交不触发空API发布。

| 交付 | 实际状态 |
| --- | --- |
| 云API | 已从真实在线valuation6叠加5源，发布`card-platform-api:0.6.2-20261019-valuation7`；镜像revision为上述源码提交，实际运行183源逐SHA吻合，health/ready正常。 |
| 合同 | formal `same-card-source-revalidated-v22`、观察 `paired-image-only-evidence-v9`、号码 `number-roles-4`、联合 `codex-joint-evidence-3`。 |
| 自动成本worker | 构建前、切换前后均保留开启；生产Compose、实际容器环境`true`和`get_settings()` true分别核验。无关闭/暂停成本worker、无手动扫描。 |
| 网页 | 无本批源码改动，保持`card-platform-web:0.6.2-20261019-valuation2`同一镜像ID；首页、球员价格页及14个静态资产HTTP200，前后静态SHA相同，不空重启或降级。 |
| 个人iPhone | 无客户端源码改动，复核已完成的`cn.cardsp.alpha.local` 1.1.10(14)覆盖安装、0830版本、启动成功/PID45759及进程原回执；唯一iphone自动化仍PAUSED。不重复打包/安装，不称本批跑了手机业务E2E。 |

发布包仅5个Python源、183源校验清单及提交元数据，无.env、私有图片、模型捕获、库存或新市场数据。Git任务补丁应用到保存在线基线能精确重建候选183源；发布不是旧分支整仓构建。在线已有成本/正背等26项分支差异保留。

发布前镜像已经构建，首个只读清单bind mount校验因权限不匹配失败；改为标准输入后，用镜像实际应用用户成功校验全部183源及合同/worker默认值，才切换运行容器。该失败与修正的执行输出均保留，没有将未校验镜像先上线。

云端备份位于`/home/ubuntu/card-platform/backups/valuation-batch04-20261019/`，数据库dump 340777598字节，SHA及路径见私有backup-receipt.json；原镜像和Compose亦保留。回滚脚本只恢复原API镜像，保留worker=true，并再次断言实际环境/settings。未在生产演练回滚或恢复数据库，不把备份说成已演练恢复。

发布后REPEATABLE READ READ ONLY+rollback复核：本批200身份、400面原图哈希、200展示投影均无漂移；原877活跃ID范围一致；分析/估值/任务历史ID无新增或移除。没有生产身份/图片/知识/价格写回或新估值任务。原显示价未被改成新成功，#543旧50和failed诊断继续分列。

私有执行证据在`.runtime/releases/20261019-valuation-batch04/`：source-package-receipt.json、built-image.json、cloud-build.txt、cloud-image-verify-retry.txt、cloud-activation.txt、runtime-final.json、drift-verification.json、public-before.json/public-after.json、mobile-existing-evidence.json、backup-receipt.json、preservation.json。完整测试与尚未修复边界见verification-batch04.md；最后77未启动。
