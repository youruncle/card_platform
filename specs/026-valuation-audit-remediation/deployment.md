# 部署 — 026

2026-10-19（Asia/Shanghai），网页与API已发布。没有执行生产身份、估值或知识批量写回，也未开始剩余677张审核。

- 代码：`codex/valuation-audit-remediation`，首批实现提交`21d3079774ba8ffa2afaf720d7fbe3007ed0feee`（追加封口与部署记录见分支最新提交），已推送origin。使用独立暂存区构造提交，工作树和主暂存区的并行修改保留。
- 最终运行镜像：`card-platform-api:0.6.2-20261019-valuation3`、`card-platform-web:0.6.2-20261019-valuation2`。
- 基线：最初cost1001构建在切换前发现线上已更新到cost1002，版本断言中止切换；重新从cost1002容器源码叠加本任务补丁后发布，保留成本与正背修复。网页源基于pair1，成本1001/1002网页镜像仅改标签。
- 容器21个本任务API文件SHA256逐一核验；正式资格v20、视觉观察v6、联合规则v3实际导入通过。API健康检查通过，网页与价格页公网HTTP200，首页12个、价格页11个静态资产返回200；历史参考标签已存在运行网页资产中。
- 实际运行设置`purchase_cost_worker_enabled is True`，Compose仍显式`PURCHASE_COST_WORKER_ENABLED=true`。正背review/flatbed来源确认能力仍开启。
- 发布前后只读核对7张恢复记录、14面图片哈希与身份字段完全一致，事务`transaction_read_only=on`。
- 网页/API先发布valuation2；随后API追加关闭旧模型类比写价出口，升级valuation3，网页资产未变。
- 无数据库迁移。云端受限权限目录保存发布前数据库备份及cost1002旧镜像；回滚只替换镜像引用，保留worker开启，不恢复旧关闭配置。

## 手机端

`1.1.10 (14)`已通过TypeScript/Vite、113项测试和Release真机架构编译，签名`YLV27B9G9U`、应用`cn.cardsp.alpha.local`。指定设备编译等待连接超时，改用同一开发签名的generic iOS目标构建成功。首次覆盖安装因CoreDevice通道超时失败；设备状态随后短暂恢复为available，重试仍报peer no longer reachable。未完成安装或启动回读，待手机解锁并通过数据线恢复通道后继续。已向用户发出连接提示。

私有构建、安装、源码、健康、图片核验日志保留在Git忽略目录`.runtime/releases/20261019-valuation-audit/`。恢复记录和原图不提交仓库。
