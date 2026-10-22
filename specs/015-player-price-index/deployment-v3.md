# 球员行情 v3 云端发布记录

2026-10-22；唯一任务见tasks.md，当前整项尚未完成，个人iPhone安装/启动仍待连接。

- API：`card-platform-api:0.6.4-20261022-prices1`，源码469683ee，镜像44cb89fcc6bf0e614d63d970b588d75ec7a374f520e7beae509faa80a53a7dc4。沿用原bailian2依赖/OCR，212 Python文件逐hash通过。
- Web：`card-platform-web:0.6.4-20261022-prices1`，源码537fcc58（UI源与56146b4d一致），镜像db1cf89afc991a438861ecf99a6517e2f983874b8012ca8a5e14650ae551674f。
- 手机：1.1.14/build18，bundle cn.cardsp.alpha.local，原个人开发签名。实际Release构建、codesign验证通过；IPA SHA4db39062b6328bc33979afef93b88fc10d9a1ea4f3b0dfe27d3c47065fcd1f80；尚未安装、读回设备版本或启动。
- 当前运行成本worker=true，行情worker=true，自动图片调用独立日限100；视觉deepseek-flash显式thinking disabled，搜索保持独立百炼qwen-plus；原环境文件未修改。

## 数据与真实验证

原452条有效旧派生价经132份审计重建为40条；三个报告日均9位有核验成交、5位有同款趋势、0三卡7日综合指数。固定验收组Kon Knueppel仍无可用成交，保留未知。117免费照片原件只提供排除证据，不能授予价格权限。后续后台新捕获/成交独立登记，数量以实际报告为准。

完整证据位于本轮`.runtime/player-price-review-20261022/`。真实公网Cookie/Bearer、排序、搜索、来源链接、单球员refresh202、账户隔离404/匿名401、窄屏首屏均通过；验证会话为明确标记的短期服务器生成会话，不是用户密码登录。后台轮次及会话撤销结果见validation-v3后续记录。

## 云端备份与回退

备份目录`/home/ubuntu/card-platform/backups/player-price-delivery-20261022/pre-switch-469683ee`含原镜像ID、完整编排、仅云端0600环境副本、完整数据库dump及manifest。数据库SHA为f20925648f57b78d1c413c7e5673fa08c0bf8be9419dbf64cfad99a7b533b238，pg_restore目录验证通过。证据独立归档`backups/player-price-verified-refresh-20261022/pre-delivery-469683ee.tar.gz`含610个逐hash验证原件，SHA97eba0a6721f77ab3033c352d20e80cd72e47cd0fea522597f0de7f2741ae9c1。签名IPA另存于delivery目录0600。

132份审计已在生产库未提交事务中逆序调用`rollback_reconciliation`，实际恢复452条原价；外层rollback后仍40条、派生指纹一致。该回退严格拒绝覆盖审计后改变的价格/报告。持续后台更新后，不能绕过指纹保护、直接恢复全库或覆盖用户新增业务数据；应先停行情与核图队列、保留成本worker=true，核对当前审计与派生差异，再做明确范围的价格修复。镜像切换仅更改api/web image与独立行情开关，沿用现有持久卷、环境和云端知识库。

原镜像：API `card-platform-api:0.6.4-20261022-bailian2`；Web `card-platform-web:0.6.4-20261022-latest1`。旧代码不具备本轮严格准入规则，恢复旧镜像属于业务降级，需同步复核行情展示。已实测首次健康检查失败自动恢复原API及原price/cost=true配置，再成功发布；不得复用历史false成本开关。

## 最终运行封存

正式后台库里两轮与詹姆斯一轮均成功；库里同查询历史页从2推进4，最终9位成交/5位趋势/0采集异常。两次临时验证会话及空测试账户已清理，撤销token公网401、本地秘密文件删除。自动核图已验证5次实际调用，日预留100后停止继续claim，40条有效价格保持。

最终增量归档`final-cloud-delivery-54ef4f61.tar.gz` SHA6a79855ff0413d29498e92c729820226aefd326153b8ed427fbd94479dac0d9e，17新原件逐hash；父归档610原件保留。公开来源/价格表最终备份`final-public-price-registry-54ef4f61.dump` SHA18432c95b30f455ec145cd6bcc3c95ca4852e8617e63d5b3fa5f1d1298577a7d，pg_restore目录通过。均在同云端私有delivery备份目录。完整证明及机器元数据为该目录`final-checkpoint.json`。

仍未通过：个人iPhone可信应用服务连接（CoreDevice4016），当前1.1.14/build18尚未覆盖安装及启动。整项任务不标完成。
