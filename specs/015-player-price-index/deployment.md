# 2026-10-12 云端与iPhone开发测试版发布

> 2026-10-16 更新：域名证书已安装，公网HTTPS、手机登录接口和网页静态资源验证通过。最新记录见 [https-deployment.md](https-deployment.md)。

## 已交付

- 云端API/Web：0.6.0，镜像 `card-platform-api:0.6.0-20261012`、`card-platform-web:0.6.0-20261012`；API健康、数据库健康、Web运行正常。
- 入口：http://1.117.65.47/player-prices；首页菜单「球员卡价涨跌」。价格读取和刷新要求登录。
- 本机API/Web也已更新，launchd保持运行；http://localhost:3000/player-prices 返回200。
- iPhone：1.1.0（build 3），已签名、安装并启动到匹配描述文件的已配对开发设备；入口「我的 → 球员卡价涨跌」。覆盖安装保留原应用数据，未卸载。
- 程序发布包仅含303份程序、共享源及迁移，未导入本机卡库、交易、图片、业务知识数据、`.env`或模型密钥。已有云端知识挂载保持。

## 仍未完成

- 公网443连接超时；等待用户在腾讯云安全组放行TCP443。
- 等待用户确认接受Let’s Encrypt证书协议，才能申请有效IP证书。已准备ACME webroot、Certbot 5.8.0及 `infra/nginx/card-platform-ip-https.conf`，尚未申请证书、切换HTTPS或配置自动续期。
- 手机预设 `https://1.117.65.47`。当前不能通过该HTTPS地址登录；未放宽ATS或证书校验。真实登录、Keychain存取、相机权限与拍摄上传尚未操作验证。
- 云端首轮262个球员状态均记录「卡淘采集失败，历史结果保留，稍后重试」，成功采集数0；不能把部署成功视为已取得真实涨跌数据。界面展示失败/数据不足，不填0%或演示值。
- 本次内置浏览器读取超时，未取得新版云端截图；公开页面、11个引用静态资源和相关接口已通过HTTP检查。原 `preview.jpg` 为首次本地功能预览，非本次云端发布证明。
- 开发描述文件北京时间2026-10-16 09:28:29到期，届时需更新签名并重新安装。未发布TestFlight或App Store。

## 验证

- 发布相关API回归74通过、3警告；Web136通过，TypeScript和生产构建通过；Mobile46通过，TypeScript/Vite构建通过。
- iOS Xcode27 Debug签名构建成功；`codesign --verify --deep --strict`通过。设备查询确认版本1.1.0/build3，安装与启动命令成功。相机插件的方法列表增加明确的 `[CAPPluginMethod]` 类型以通过当前SDK编译。
- 云端9项发布检查通过，见 [cloud-checks.json](cloud-checks.json)：就绪及版本、匿名拒绝、移动能力、Bearer登录、已有卡库不可见、账户价格范围、刷新与日期校验、会话撤销、Web HttpOnly登录Cookie。
- 检查生成并清理2个临时账户；前后原业务数量一致：4账户、627卡、308交易。未使用真实用户密码或测试真实卡照片。
- 本次未重跑完整后端；上一轮127项历史失败尚不能宣称修复，见 [validation.md](validation.md)。

## 数据库与发布备份

服务器根目录 `/home/ubuntu/card-platform`；Compose文件 `infra/compose.tunnel.yaml`，环境文件 `.env.cloud`。

- 部署前数据库：`/home/ubuntu/card-platform/backups/db-20261012T002945Z.dump`。
- 已保存Mac独立副本：`.runtime/releases/20261012/cloud-before.dump`；双方SHA256一致：`8e73474a18c7e08ed4c12054b873e2967cde1f06cea64283e04322ee80a9459e`。
- 程序、编排及环境备份：`/home/ubuntu/card-platform/backups/release-0.6.0-20261012/`；目录700、环境备份600。
- 原镜像保留，并增加标签 `card-platform-api:before-0.6.0-20261012`、`card-platform-web:before-0.6.0-20261012`。
- 本机旧Web产物：`.runtime/releases/20261012/web-build-before`。
- 本机签名测试安装包：`.runtime/releases/20261012/CardPlatform-1.1.0-build3.ipa`，包含设备开发描述文件，仅本机保存。
- 云端发布源：`/home/ubuntu/card-platform/releases/0.6.0-20261012`，已校验manifest；传输包SHA256：`39acd84d4f50d3e1e42d2c5427960508812614cb6132a540bdf1e7dbab76b378`。

应用了尚未应用的0078市场关联、0079单面录卡、0079公开源目录、0080持仓读取索引、0081价格统计迁移；保留服务器已应用的兼容迁移及原有持久卷。原扫描、识别、估值、采购成本worker开关保持关闭，只启用新的价格worker。

## 程序回退（必要时人工执行）

先停止新API，恢复备份编排并使用原镜像；保留增量数据库字段和新业务记录。不要直接覆盖数据库备份，否则会丢失发布后的业务写入。数据库恢复应在独立数据库验证并明确数据恢复时间点后处理。

```sh
cd /home/ubuntu/card-platform
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml stop -t 60 api
sudo cp backups/release-0.6.0-20261012/compose.before.yaml infra/compose.tunnel.yaml
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml up -d --no-build api web
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml ps
```

本次未变更`.env.cloud`；正常程序回退无需恢复环境文件。新单面录卡入口需随客户端回退关闭，旧worker保持关闭，不能将已存在单面记录改回双面。HTTPS后续切换应单独留备份；现有`nginx.before.conf`为发布前HTTP配置。

## HTTPS后续步骤

确认协议和安全组后：使用Certbot webroot申请`1.117.65.47`的shortlived IP证书 → 安装Nginx HTTPS配置并验证公网可信TLS → 将API允许origin、Secure Cookie及Web站点地址切换HTTPS → 配置并验证系统级证书自动续期与Nginx重载 → 重跑线上检查和手机连接验收。HTTP服务目前保持运行，未提前强制跳转不可达的HTTPS。

## 手机登录超时排查（2026-10-12）

截图客户端使用HTTPS IP地址，登录报「请求已取消或超时」。独立复现：同主机HTTP能力接口200（约0.21秒），HTTPS443连接5秒超时、HTTP状态000。服务器ss仅显示Nginx监听80，无443监听；IP证书文件不存在；容器API/PostgreSQL健康，容器内readiness200。故障定位在HTTPS传输入口尚未部署，请求无法进入登录接口，不是已确认的密码错误或数据库故障。公网443不可达，但本次没有读取腾讯云安全组规则，不能单凭超时断定具体阻断规则。

客户端MobileApi普通请求30秒中止，原生connect/read timeout也均30秒，AbortError统一呈现草稿相关文案，因此登录也显示了不适合登录场景的草稿提示。预设尚未就绪的HTTPS地址导致此测试包目前无法联网登录；完成443和有效证书/监听配置后需要再做真机登录回归。此次仅分析，未更改客户端或尝试使用截图中的账户登录。
