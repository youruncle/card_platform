# 手机 P0 新版发布

2026-10-17，北京时间。用户明确授权发布到手机和网页。

## 手机

- 1.1.6 / build10，Bundle ID `cn.cardsp.alpha.local`，沿用现有开发团队和签名。
- 已覆盖安装到连接的 iPhone 16 Plus，未卸载或清除应用数据；devicectl 从设备读回 1.1.6/build10，随后启动成功。
- Mobile全套107项测试、TypeScript/Vite生产构建、Capacitor iOS同步、独立DerivedData的Xcode Release构建通过；codesign deep/strict校验通过。
- 已对签名App资源核对成本、身份、估值、交易、回收站等P0按钮字符串。
- 开发描述文件覆盖该设备，有效至北京时间2026-10-23 00:56:53。未上传TestFlight/App Store。
- IPA `.runtime/releases/20261017-p0/CardPlatform-1.1.6-build10.ipa`；SHA256 `501a8f434a4f946b41800084af7fcf472ad414a899ad4bf35c5e38c9ee5f637b`。

## 网页

- 本轮核对期间，既有首屏优化发布流程已将线上Web从 `card-platform-web:0.6.1-20261016` 切换为 `card-platform-web:0.6.1-20261017-firstpaint1`。本轮采用该已验证产物，未再覆盖另一发布流程。
- 线上已包含网页版原有P0操作及本日首屏优化；没有将工作区随后进行的独立球员行情改动混入此Web产物，也没有发布未完成的自动成本队列或数据库迁移。
- 线上API保留 `card-platform-api:0.6.1-20261017-library1`，OpenAPI核对P0成本确认、身份审核、交易、回收站接口均存在。此次P0手机集成复用这些接口，不需要后端或数据库变更。
- 公网 https://cardsp.cn 首页、/player-prices、/v1/client-capabilities均200；首页引用12项JS/CSS全部200；匿名auth/me、回收站、卡淘账号状态均401。
- 从线上运行镜像取得真实react-loadable manifest，以公网真实JS资源配合mock私有API执行生产浏览器复合场景：1项通过、无未捕获页面错误。测试登录样例未创建线上真实会话或改写卡库。
- 网页原发布备份/回退点沿用 `.runtime/releases/20261017-first-paint/activate-cloud.sh` 和服务器 `backups/first-paint-20261017/`；本轮不修改云端Compose、密钥、卷或数据库。

## 验证边界
真机安装、版本读回和启动已验证；没有代用户登录、执行真实卡淘订单查询或短信验证、记录真实交易、删除真实卡片，也未完成真机所有页面操作验收。

本地发布日志与devicectl结果保存在忽略目录 `.runtime/releases/20261017-p0/`，不提交设备完整应用清单。未提交或推送当前工作区其他改动。
