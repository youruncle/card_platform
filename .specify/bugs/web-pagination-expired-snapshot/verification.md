# 网页分页快照失效

- 症状：第一页停留后点击下一页，页面仍显示第一页。
- 根因：服务端 SnapshotCache 默认 TTL 为 60 秒；前端收到 snapshot_expired 后固定重试 page=1，丢失用户请求的页码。
- 复现：Dashboard 组件通过 fetch 边界模拟首次加载成功、下一页返回 409 snapshot_expired；修复前无法找到第二页卡片，新增回归用例失败。
- 最小修复：失效后使用 requestedPage 和空 token 重建快照，继续导航；服务端已有页码上限裁剪。
- 验证：分页专项 2/2 通过，覆盖失效恢复、后续 token 延续、排序切换。TypeScript typecheck 通过。
- 相关测试整组：41 项中 40 通过，另有 search superseding the first load still starts initial account metrics once 失败（汇总请求次数 0，预期 1），属于首次加载被搜索覆盖的汇总初始化路径，本次未修改该路径。
- 限制：通过组件与模拟 HTTP 响应验证，未在真实登录浏览器或部署环境回放；未部署。

## 2026-10-17 线上复现与发布

- 实际站点为 https://cardsp.cn ，此前修复仅在本地，线上仍运行旧构建。
- Chrome 已登录真实账户复现：第 4 / 30 页（877 张）停留超过 TTL 后点击下一页，实际回到第 1 / 30 页。
- 仅修改服务器 page.tsx 的失效重试页码；保留服务器现有其余代码，未上传本地其他未提交功能。备份源码 /tmp/card-page-before-pagination-fix.tsx，旧镜像 card-platform-web:before-pagination-fix-20261017。
- 服务器 Docker Web 生产构建成功，已仅重建 Web 容器；API 和数据库未重启。线上 JS 已从 page-3dfd7c4fcff2bc59.js 更新为 page-6e7a67a569473942.js，重试使用目标页码。
- 本地分页专项 2/2 与 typecheck 通过。相关 41 项测试本轮 40 通过，selected retry preserves duplicate choices and retries only the unmatched card 失败（找不到 dialog）；此前汇总初始化用例已通过，工作区同时存在其他修改。
- 线上真实界面最终验证通过：刷新新版本后第一页→第二页；等待 63 秒以上使快照失效，下一页成功显示第 3 / 30 页，快照时间从 22:14:20 更新为 22:15:46，未回到第一页。
