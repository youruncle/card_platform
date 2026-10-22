# 球员行情 v2 双端发布

2026-10-18，北京时间。用户明确要求发布本轮改动，并约定后续“部署到线上”默认同时包含网页端和手机端及所需API。

## 已发布

- 公网 [球员行情](https://cardsp.cn/player-prices) 已切换新版。Web与API镜像均为 `card-platform-{web,api}:0.6.1-20261018-market2`；API健康。
- iPhone 16 Plus已覆盖安装「我的卡库」1.1.7/build11，设备读回版本一致，解锁后启动成功。沿用个人开发签名和Bundle ID `cn.cardsp.alpha.local`，未卸载、未清除数据、未上传TestFlight/App Store。验证完成后用户可断开手机。
- 手机签名App中已核对30日行情及单球员更新资源；描述文件包含该设备，有效至北京时间2026-10-23 00:56:53。

## 发布范围与数据保护

Web以最后发布的firstpaint源码包为基线，仅叠加本轮价格页面、组件、价格API客户端及共享价格类型；沿用已验证的Dockerfile/workspace构建修复。未把工作区未发布的成本功能或其他业务代码混入Web。

API基于线上 `0.6.1-20261017-library1` 镜像，仅覆盖player_prices的api/discovery/domain/repository/service五个模块，同时更新源目录与实际加载的虚拟环境site-packages。保留现有库分页、扫码识别、估值及worker开关。

没有新增数据库迁移，没有上传本机账户、卡库、图片、知识数据、.env或模型密钥，没有以本机数据库覆盖线上。

## 验证

- 发布前价格后端41项通过（包含真实PostgreSQL回滚事务），Mobile107项通过；Web上一轮151项通过，本轮Linux镜像重新执行Next编译、类型和静态生成成功。
- Mobile TypeScript/Vite、Capacitor同步、Xcode Release构建、codesign deep/strict、设备安装、版本读回和启动通过。
- 云端切换前预览容器验证新版页面及11项静态资源200、匿名行情401，构建代理均指向`http://api:8000`，模块可导入。
- 切换后API内以READ ONLY事务校验真实行情、报告日截止、陌生账户隔离、范围外刷新无写入及五个实际加载模块SHA256；匿名读取/刷新401，OpenAPI存在可选player_id刷新参数。
- 该次真实只读审计：全体持仓篮球对象并集315人，有确认成交83人、有30日卡款趋势0人、有综合指数1人；237人已排队，来源错误0。数量是检查时点状态，不等于某一账户的实时数，也不代表新增识别召回。
- 公网新版页面、11个引用JS/CSS、功能能力接口200；匿名行情及auth/me401。
- 公网真实JS/CSS的Chromium宽屏/窄屏、搜索/展开/单球员刷新交互通过；无pageerror和横向溢出。浏览器私有API使用本地只读导出和模拟刷新，未登录真实账户或写真实卡库；不等于真实账户HTTP端到端更新验证。
- 原版也可复现的26项卡淘provider回归失败仍未解决，清单及基线见validation-v2.md；未重跑整个后端历史套件。

## 备份与回退

- 服务器发布目录：`/home/ubuntu/card-platform/staging/player-market-20261018/`。
- 备份目录：`/home/ubuntu/card-platform/backups/player-market-20261018/`，目录700；包含切换前Compose、原镜像名、原价格源码与完整数据库dump（600，333175497字节，pg_restore --list通过）。
- 旧镜像额外保留`card-platform-api:before-player-market-20261018`及`card-platform-web:before-player-market-20261018`。
- 回退只恢复当前Compose中api/web的原image字段，然后使用现有.env.cloud执行`docker compose up -d --no-build --no-deps api web`；不恢复数据库、不删除卷、不覆盖其他服务/worker设置。切换脚本内置健康失败回退。
- 本地IPA：`.runtime/releases/20261018-player-market/CardPlatform-1.1.7-build11.ipa`；IPA SHA256：`f7bbd4ca559a827c997afcf1f4f7374123550f575ede44d0f768572b6309437b`。校验清单、构建/设备结果、公网测试和截图均在同目录。服务器备份已完整复制到该目录，双方SHA256一致：`9797f69389ccc1d218bf9c02b809408f8154bbf236ad7304f79408fe4a27c1e6`。

## SSH部署登录

经用户提出长期免扫码需求，本机新增独立Ed25519部署密钥；私钥只存本机`~/.ssh/card_platform_deploy`（0600），没有上传私钥。服务器ubuntu的authorized_keys追加带`restrict`选项的公钥并保留原条目；修改前另存同目录备份。

以全新连接、关闭连接复用、BatchMode与仅publickey认证实测登录成功。未关闭腾讯云扫码、未开启密码登录、未更改sshd/PAM；终端仍可能显示扫码横幅，但公钥认证不需要扫码。后续使用`ssh card-platform-deploy`，30分钟连接复用。约定已记录于AGENTS.md与docs/ai-development-workflow.md。

## 未验证

未操作真机全部页面、相机或Keychain，未代用户登录、交易或删除卡片；未验证多日连续日更和30日指标稳定性。来源仍有预算、身份校验仍严格，低样本不补零或伪造成交。
