# 原877最终共性发布 — 2026-10-19

17:46:14（Asia/Shanghai）API已切换`0.6.2-20261019-valuation-final877`，镜像`sha256:7cd87a088aaeb672b3f920aadc04dd7f8b64ad16dc396e0a5ee3183946bec0db`。

以当时实际quality1镜像`sha256:f1502e2489b01a5c8823657ce47fecddd9707e53f4c2d8d9e017fb6b6e3444f1`为父源，仅叠加4个域文件；其余183源逐SHA保留。未将主工作区成本hunk或旧估值整镜像覆盖线上。构建、切换均校API/web镜像ID、StartedAt与Compose SHA，构建后和运行容器再次校187源；全源manifest SHA为`d3b4044125864d3a65ed14ae0434fe32d677172c114b55f016e57a58222d6af7`。

formal仍v22、paired image仍v9、number roles仍4、joint仍3；读合同为`market-price-publication-read-v2`。成本worker容器env=`true`且`get_settings().purchase_cost_worker_enabled is True`；卡3/图6/DB池8，来源原单并发锁代码不变。API health与ready均200；本次未触发回滚，回滚脚本也保留worker=true并实核设置。

网页无源码变化，原quality1网页镜像和启动时间保持，未作空重建/重启。实际`https://cardsp.cn/`及`/player-prices`均200，分别12/11项静态资源均200并留SHA；`/v1/client-capabilities`代理200，运行网页的rewrites仍指向`http://api:8000/`。

手机无源码变化，沿用个人iPhone已验证`1.1.10 (14)`，本次没有重新安装或新手机E2E。原安装/版本/启动收据在`.runtime/releases/20261019-cost100/mobile-followup.json`及其device-checks引用；没有创建自动化或覆盖等待链路。

上线后实际PortfolioRepository前后差异、真实有价/无价控制及原877十张表摘要均验证，详见verification-final877.md。仅读取投影改变，未删改历史、批量改身份/手工价/知识或启动重估。最后77密封哈希保持；automation-3/4及父follow-up未修改，原活动由父方独立验收后关闭。

云端构建/切换目录`/home/ubuntu/card-platform/staging/valuation-final877-20261019`，原Compose备份在同服务器`backups/valuation-final877-20261019`；私有本地preflight/build/activation及public-after收据位于`.runtime/releases/20261019-valuation-final877/`。
