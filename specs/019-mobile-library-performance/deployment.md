# 卡库性能更新发布（2026-10-17，北京时间）

## 交付

- 云端 https://cardsp.cn API 镜像：`card-platform-api:0.6.1-20261017-library1`，状态healthy。
- 基于当时正在运行的`0.6.1-20261016-pricefix1`镜像，仅覆盖本轮`api.py`与`portfolio_sort.py`（源目录及实际site-packages两处）。已对比线上原文件，差异仅本轮分页优化；价格采集修复保留。Web、数据库结构、现有卷和worker配置未改。
- iPhone 16 Plus 已覆盖安装「我的卡库」1.1.2/build6；devicectl安装成功并从设备读回版本，保留应用数据。Release构建与codesign严格校验通过。
- 初次两次启动因锁屏被系统拒绝。用户解锁后重试，devicectl成功启动`cn.cardsp.alpha.local`，退出码0。真机安装、版本读回和启动已验证；登录、滚动、相机或Keychain交互尚未操作验证。
- 个人设备开发描述文件有效至北京时间2026-10-23 00:56:53。本次为开发设备分发。

## 验证

- 发布前Mobile69项单测通过；TypeScript/Vite生产构建及Capacitor同步通过。
- 发布前复跑临时PostgreSQL相关测试：45通过、3可选合成性能基准跳过，1个已有Starlette/httpx弃用警告。
- 线上在显式READ ONLY数据库事务内验证：默认成员快照首批30张，与数据库全库稳定顺序一致；第二页对应后续30张且无重复；随机其他账户复用token被409拒绝。仅输出布尔结果、数量和模块哈希，不生成真实账户会话，不写卡库。
- API内readiness200、portfolio_sort能力开启、匿名卡库401；公网首页200、功能接口200、匿名新分页请求401。
- 服务替换启动窗口内早期公网探针曾返回500；服务就绪后上述探针全部符合预期，最终API healthy。
- 原后端全套测试的失败及未完成范围沿用verification.md，本次未重跑全套；未测线上提速比例。

## 产物与回退

- 本地IPA：`.runtime/releases/20261017-library/CardPlatform-1.1.2-build6.ipa`；SHA256 `09bff3f9249ef399f50f493a414301f5fb0e672862aa0fe5778a266d8657eb9e`。
- 云端发布文件：`/home/ubuntu/card-platform/staging/library-20261017/`。
- 云端备份：`/home/ubuntu/card-platform/backups/library-20261017/`，目录700，含原Compose、两个原模块和数据库自定义格式备份；pg_restore --list检查通过。数据库备份SHA256 `8d1c24418865c4d16d0410be4957aa1353ed27faf226d0276a53d25f04d3d720`。本次未复制这份备份到离机位置。
- 实际加载模块SHA256：api.py `57a899cc27f54ff6676c4964dae34e5bd97317ffed972241c705cf3b6c2a522b`；portfolio_sort.py `9df60c456306d6c41ed4249c342d7bdf25e97c329d1844abe0ea0715ebf44dd1`，与发布文件一致。
- 必要时程序回退：在服务器部署目录恢复`backups/library-20261017/compose.before.yaml`到`infra/compose.tunnel.yaml`，使用原`.env.cloud`执行`sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml up -d --no-deps api`，恢复原pricefix1镜像；不恢复数据库、不删除卷。新客户端在旧服务上回落到原完整快照实现。
