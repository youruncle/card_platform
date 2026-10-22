# 云端估值长期显示更新中

日期：2026-10-17（北京时间）

## 复现与根因

cardsp.cn 的 ID 885 估值任务于 2026-10-17 01:57 创建，排队超过 19 小时，attempt_count=0。云端 Compose 在 API environment 中明确设置 VALUATION_WORKER_ENABLED=false，容器环境也为 false。该配置使 API 正常服务请求但不启动估值消费者。此前为何关闭，未查明。

## 修复

云端备份 infra/compose.tunnel.yaml 为同目录 compose.tunnel.yaml.before-valuation-worker-20261017，将估值进程设置改为 true，以现有镜像仅重建 API 容器，并保持并发为 1。本地 infra/compose.tunnel.yaml 同样显式设置 true，避免继承环境文件中的关闭值。未修改估值规则或手工覆盖估值数据。

## 真实任务验证

- 容器环境 VALUATION_WORKER_ENABLED=true，API 健康状态 healthy，网站 HTTP 200。
- 原任务自动完成市场数据刷新和估值流程，attempt_count=1，无 error_message。
- 于北京时间 21:38:32 结束，状态 insufficient_evidence，已退出所有运行状态。
- 检索召回 26 条候选，26 条均被身份冲突规则拒绝，原因“检索到的成交均与当前卡片身份存在硬冲突”。没有生成无依据价格。
- 活跃估值任务数为 0。
- git diff --check 通过。此次为部署配置修复，未运行应用全量单测，未通过登录浏览器目视验证；终态对应前端“证据不足”，由数据库终态与现有渲染分支确认。
