# 依据与选择

复现与 traceback 见 [.specify 评估](../../.specify/bugs/web-canon-cloud-bridge/assessment.md)。Swift CLI 已返回真实 Canon 能力，现有云 upload 可以独立于设备工作。

采用“浏览器 → 回环助手取图 → 浏览器认证云上传”：复用已有会话归属和上传门禁，不引入云到个人电脑的公网端口、反向隧道、扫描任务队列或存储凭据。直接把云 capture 改成 localhost 不成立，因为本机 API 和云端不是同一数据库或登录域。

Chrome 142 起访问本机网络有权限提示，需显式连接入口与可重试说明；支持旧 PNA OPTIONS 头。依据：[Chrome 官方本地网络权限说明](https://developer.chrome.com/blog/local-network-access?hl=zh-CN)。助手仅服务固定回环地址，不能以放宽浏览器安全策略或公开端口解决连接。
