# 第三批视觉召回接续部署 — 026

2026-10-19 06:24（Asia/Shanghai）。已发布可实证的有限通用视觉观察改进，**#261仍误拒，T027及下一批接续保持未通过**；不将API发布或5个正常成功称第三批整体优化完成。

- 实现提交`ce38d3deef376d0d86c4f9d42a8795ee3d266077`，已推送`codex/valuation-audit-remediation`。独立index仅8份任务文件，主HEAD/index及已有未提交修改保留；不切主分支、不整仓发布。
- API实际`card-platform-api:0.6.2-20261019-valuation6`，image ID `sha256:3b8310055303dd271dcb9d13fff4e183979936fa4786dbc180f9b391ec2a5b23`，运行revision `ce38d3deef376d0d86c4f9d42a8795ee3d266077`，Docker healthy。基于切换前真实valuation5镜像叠加1份comparable_image_verifier.py；构建前/切换前均逐SHA核183份基线，切换后183份均符合清单，另外182份与发布前一致。
- 运行观察合同v9、正式同卡v21、号码角色3、联合规则3；源码SHA与最终统一19组及独立审查冻结的`82ca4c56587f126c64872c04445cdbb2a8afb50fee738d6db1392fddde030bda`一致。health=ok、ready=ready、角色/查询smoke通过；pair_integrity及flatbed_source_confirmation能力仍true。
- 成本worker：生产Compose显式true，运行环境PURCHASE_COST_WORKER_ENABLED=true，Settings为True。构建/切换前后均核真值；回滚脚本只换API镜像并断言保留true。本次未触发回滚。
- 服务器完整数据库备份336,716,829字节及原Compose保留在私有backups/valuation-batch03-visual-20261019。没有迁移或手工生产业务写入，不重估、不导入知识/市场价、不改身份、评级或图片。
- 发布前后READ ONLY/REPEATABLE READ事务rollback，200张身份/持有/updated_at/图URL及SHA逐行相同，396面SHA、原877活动集合一致。四个缺背继续缺失；不将该检查当成成交/品相或手机用户路径认证。
- 网页源无变化，继续`card-platform-web:0.6.2-20261019-valuation2`，image ID `sha256:a89995837f1e0dc9c0f622464cc4fcc300305041df9dac8231f6e6cb2ab1eede`；公网首页、价格页、能力接口200，去重14个静态资产均200且逐SHA与切换前完全一致。
- 原审核封存6,730文件复核未变；主HEAD保持26573d431d43ac702df844df9a82fdddcc818397，主index SHA保持31573ec977fdadb1c14f7d6a7bb45c5424f6e3dcc54e7f39906e7108b7245770。

## 验证和保留问题

最终候选19组26次真实模型回执完整保存，严格v7基线19组0个T0；最终v9取得5个正常T0，8个明确异版/异号/异语种负例及1个缺候选背图对照全部拒绝。独立审查实际看5组双方正背20图并复算所有请求/响应SHA、压缩上传SHA、FK、绑定、归一化、facts和formal，errors=0；不对模型自由文本字段或细微品相背书。

#261主比较same，独立fixed_color仍different，另有226、233、344:0、830正常误拒；不硬改结果，不继续为得到same调提示。#836标题“轻印”和模型“封装内”描述错误保留；#865旧赛季错误未写回，版权年不推定发行赛季。200份独立价格仍null，109身份/绑定研究未解、4缺背、2受阻、864无评级标签仍保留。

工作树范围180 passed，实际Git树独立导出并叠加生产基线181 passed，独立审查75+1项passed；隔离DB17 passed（最后提示微调前）。较广API中止于120个失败：实际生产基线与最终候选逐项均119 failed/1 passed，完整集合一致；年份保护的同义措辞测试兼容后通过，不宣称完整API全绿。最终v9还重新回放363条历史保留案例并独立核验，全部rejected，27条缺原provider不补造。

详细模型分阶段矩阵、旧harness与#344来源顺序更正、试验及失败名单见verification-batch03-visual.md；封存目录未覆盖，失败/probe未删除或混为统一候选结果。

## 手机和接续

沿用唯一iphone自动化及.runtime/releases/20261019-cost100/mobile-followup.json，只同步当前valuation6服务器证据；已安装并回读1.1.10(14)、个人签名渠道、设备与安装记录、installed_awaiting_unlock及启动待解锁状态均保留。本次未新构建、未重装、未重新核设备，也未声称手机启动完成或为等待设备回滚云端。

剩余277未启动，未新建第四批聊天/批次。本轮源码发布可验收，第三批整体优化和下一批门仍未关闭；#261等需后续材质/多角度对照与真实负例矩阵后再判断。

私有执行回执：.runtime/releases/20261019-valuation-batch03-visual/中的release-verification.json、runtime-final.json、model-verification.json、review/FINAL_REVIEW.md、review/final-integrity-v9.json、cloud-build/activation.txt、production-before/after.json、public-before.json、public-verification.json、retained/verification.json、preservation.json及mobile-followup-update.json。账号/库存、DB备份、原图、原始响应和密钥未提交。
