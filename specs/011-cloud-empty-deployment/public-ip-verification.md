# 公网 IP 测试入口验收

用户明确授权暂不使用域名，通过 HTTP 公网 IP 访问，并已放行 TCP 80。

- Nginx 配置检查通过，systemd 自启动启用。
- 从 Mac 访问 http://1.117.65.47 首页 HTTP 200，匿名 /v1/auth/me HTTP 401。
- 临时账户通过公网入口测试图片匿名/其他账户/所属账户分别 401/404/200；同源 Cookie 转发、带 Origin 的上传和旋转、HEAD、no-store 缓存策略、旧 URL 和软删除图片访问通过；生成的数据均清理。
- API 保持 AUTH_REGISTRATION_ENABLED=false；同时允许公网 IP 与原 SSH 隧道 origin。
- 前端重新构建通过，站点元数据使用公网 IP；API 和 PostgreSQL 无主机端口映射，Web 仅监听 127.0.0.1:3000。
- HTTP 不加密、摄像头安全上下文限制已向用户说明；未部署 HTTPS，未运行真实模型识别或全套业务回归。
- 新建一次性凭证经公网登录返回 200 并取得 Session Cookie；测试后账户与会话清理，最终仅保留 1 个 admin。
- 最终 Mac 外网请求 HTTP 200，确认返回 Card Alpha 应用 HTML 且站点元数据为公网 IP。
- Codex 内置浏览器打开公网入口超时，未宣称完成该入口的浏览器视觉验收；独立 HTTP 请求及公网登录/API 测试通过。
