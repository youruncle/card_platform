# cardsp.cn HTTPS证书安装（2026-10-16）

## 结果

证书已安装并由Nginx加载，监听TCP443。服务器内部和公网可信TLS均通过；公网价格页、11个静态资源及手机登录接口已验证。用户随后确认App登录成功，云端审计于北京时间2026-10-16 00:44:56记录成功；真实密码由用户自行输入，未向代理披露。

手机服务地址应填写 `https://cardsp.cn`；该域名证书不包含IP地址，原 `https://1.117.65.47` 不能使用此证书。

## 证书与部署

- 来源：用户下载的 `cardsp.cn_nginx` 证书包，证书与私钥公钥摘要匹配。
- 域名：`cardsp.cn`、`www.cardsp.cn`；两者A记录均指向1.117.65.47，无AAAA记录。
- 签发机构：TrustAsia DV TLS RSA CA 2024。
- 有效期：北京时间2026-10-13 23:00:00至2027-01-11 22:59:59。
- 安装文件：`/etc/nginx/ssl/cardsp.cn/fullchain.pem`、`private.key`；证书目录root:root/700，私钥root:root/600。私钥未加入仓库或公开下载。
- 配置：`/etc/nginx/sites-available/cardsp.cn`，由sites-enabled同名链接启用；仓库模板为 `infra/nginx/cardsp.cn.conf`。
- 新域名HTTP跳转HTTPS；原IP的HTTP入口保留。HTTPS代理为返回Cookie追加Secure标志。
- API的ALLOWED_ORIGINS补充两个HTTPS域名，避免浏览器登录后操作被来源校验拒绝；已重新创建API并通过健康检查。其他worker开关和镜像版本保留，未变更数据库结构或环境密钥。
- Nginx通过配置检查后平滑reload，未停止服务。

## 验证证据

- `nginx -t`成功；服务器TCP443已监听。
- 服务器curl以域名SNI和系统信任链校验，主域名能力接口和www首页均200，`ssl_verify_result=0`，未使用跳过验证选项。
- 主域名HTTP请求返回308到HTTPS。
- 使用一个自动生成的临时账户，经API容器→宿主Nginx→Web→API的真实HTTPS链路验证手机Bearer登录、价格列表账户隔离、退出撤销；网页登录Cookie包含HttpOnly及Secure，携带HTTPS Origin的退出成功。
- 临时账户已清理，原账户/卡/交易数量前后一致。结果见 [https-checks.json](https-checks.json)。未使用用户真实密码。
- API与PostgreSQL健康，Web运行正常。
- 初次外部连接443超时；随后公网443恢复可达并再次验证成功。服务器UFW未启用，INPUT策略ACCEPT，现有云镜防火墙规则仅限制部分来源的SSH22。未修改这些防火墙规则或读取云控制台安全组规则。
- 最终外部Mac通过系统信任链验证主域名和www：价格页200、11个静态资源全部200、能力接口200、匿名价格401；向手机登录接口提交明确无效的测试凭证返回预期401（请求已到达鉴权，未超时）。HTTP价格路径308跳转到同路径HTTPS。公网检查未使用真实用户凭证。

## 备份与回退

部署前备份位于服务器 `/home/ubuntu/card-platform/backups/tls-20261016/`（目录700）：`nginx.before/`及`compose.before.yaml`。

若仅回退新域名TLS：移除 `/etc/nginx/sites-enabled/cardsp.cn` 链接，执行 `sudo nginx -t` 后平滑reload；保留证书和备份便于恢复。原IP HTTP站点未修改。API仅增加两个HTTPS来源，如需回退，可移除这两个新增来源后重新创建API，避免覆盖后续其他编排变更。

## 待完成

1. 域名和手机实际登录验收已完成；原开发签名在2026-10-16 09:28:29到期，届时需另行更新签名安装。相机、上传和Keychain重启恢复未进行本次真机验收。
2. 这次安装的是下载的域名证书，未配置自动续期；到期前续期并替换证书后需再次验证及reload。
