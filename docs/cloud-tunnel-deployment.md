# 空白云端部署（SSH 隧道）

本次仅发布程序、迁移和必要系统字典，不恢复本地数据库或图片。导出程序会将 NBA JSON 词库变为空结构，不导出恢复成交资料；代码内置规则仍保留。知识管理页面不等于已导入知识库。

服务器目录 `/home/ubuntu/card-platform`。Compose 文件 `infra/compose.tunnel.yaml`，服务器环境文件 `.env.cloud`，权限 600。仅 Web 绑定 `127.0.0.1:3000`，API 和 PostgreSQL 不发布端口。模型密钥暂未配置。SSH 隧道地址 `http://localhost:13000`。

## 发布

在仓库根目录执行 `python3 scripts/package-cloud-empty.py`，产物 `/tmp/card-platform-cloud-release.tar.gz`。此包来自当前工作树，包括未提交程序，不包括本地运行数据。部署使用腾讯云 Docker Hub 镜像与 PyPI 镜像，pnpm 固定为 11.25.0。包中的依赖脚本权限明确允许 esbuild/sharp。

服务器安装 Docker 和 Compose；解压发布包，生成独立 `.env.cloud`，至少包含随机十六进制 `POSTGRES_PASSWORD`。不要上传本地 `.env`。

```sh
cd /home/ubuntu/card-platform
sh scripts/cloud-migrate.sh
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml build
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml up -d
```

首次初始化会清理迁移提供的知识来源，仅允许空用户、空卡库执行，并写入一次性标记。已有用户时拒绝清理。运行正常后不要删除数据卷，不使用 `down -v`。

## 访问与注册

```sh
ssh -i /Users/song/Downloads/song_key.pem -N -L 13000:127.0.0.1:3000 ubuntu@1.117.65.47
```

打开 http://localhost:13000 。首个注册账户成为管理员。隧道模式 Cookie 不设 Secure，因为浏览器访问本机 HTTP；传输由 SSH 加密。管理员注册后，将 Compose 的 AUTH_REGISTRATION_ENABLED 设为 false，再重新创建 API。公开 HTTPS 上线前必须改为 Secure Cookie 并重新审核注册流程和入口安全。

## 运维

```sh
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml ps
sudo docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml logs --tail=100 api
sh scripts/cloud-backup.sh
```

容器自动重启，日志限制 3×10MB/服务。备份脚本当前提供数据库备份；图片和 secrets 卷也必须备份并复制到服务器外。单机本地备份不能防整机丢失。模型密钥可添加到服务器 `.env.cloud`，随后 `up -d --force-recreate api`。不要将密钥提交仓库。

限制：云端无法使用 Mac 原生 OCR 和直接控制本机 USB 扫描仪；外部模型、市场数据源和真实扫描样本另行验证。每类 Worker 单并发，不等于整机所有类型任务共用单个并发槽。

## 卡库图片访问控制（2026-10-05 已更新）

`/uploads` 已改为 Session 与账户归属鉴权：匿名返回 401，其他账户返回 404，只有当前未删除卡牌的所属账户可读。图片响应设置 private,no-store，保留原 URL、缩略图、高清查看与旋转。API 已部署；公网仍未开放。以前已经下载或缓存到客户端的图片无法追溯撤回。公开上线仍需 HTTPS、域名备案和其余安全核对。


## 注册状态（2026-10-05）
正式管理员已自行注册并核验，当前服务器及仓库编排均已关闭注册。全新安装如需初始化，须在仅 SSH 可访问时临时开启注册，完成管理员创建后立即关闭。现有账户和会话保留。

## 公网 IP 测试入口（2026-10-05 已开启）

用户已在腾讯云放行 TCP 80。服务器 Nginx 使用 `infra/nginx/card-platform-ip.conf`，监听 80，仅匹配 Host `1.117.65.47`，转发到 127.0.0.1:3000。访问 http://1.117.65.47 。API 来源白名单包含该 HTTP origin，并保留原 SSH 隧道来源；注册关闭，Cookie 在 HTTP 测试阶段不设 Secure。前端 metadataBase 已更新为公网地址。

Nginx 由 systemd 开机启动。站点文件 `/etc/nginx/sites-available/card-platform-ip`，修改后先 `sudo nginx -t`，再 `sudo systemctl reload nginx`。默认站点已停用，未知 Host 返回 444；上传上限 220MB，代理读取超时 300 秒。API 与数据库无主机公开端口，Web 仍只绑定回环地址。

此入口为普通 HTTP，登录与业务数据未加密，浏览器摄像头受安全上下文限制；长期使用应配置 HTTPS。需要关闭公网入口时停止 Nginx 并在腾讯云关闭 80，SSH 隧道仍可用。此次变更前保留 `infra/compose.tunnel.yaml.before-public-ip` 和 `card-platform-cloud-web:before-public-ip`，回退时也应同步恢复本地编排配置。
