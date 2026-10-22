# 配置修复

2026-10-17：通过SSH同步现有本机识别服务的必要VISION_*配置及CARD_SEARCH_MODEL到云端.env.cloud，文件权限600；未同步本地数据库、图片或整份环境配置，密钥未输出到日志。云端小调用预检通过。

Compose仅将SCANNER_WORKER_ENABLED改为true，保持单并发；benchmark/估值/采购成本worker继续关闭，价格worker配置保留。使用现有library1镜像重建API容器，无代码或数据库结构变更。

修改前受限备份位于服务器/home/ubuntu/card-platform/backups/mobile-recording-worker-20261017/（目录700、文件600），含env.before与compose.before.yaml。若需回退配置，恢复两文件到.env.cloud及infra/compose.tunnel.yaml，再重建API服务；不回退数据库或已完成任务。

初次配置脚本因生成的远端脚本语法错误未执行写入；修正脚本后同步成功，Compose配置解析通过。
