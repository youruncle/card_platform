# 扫描提交后卡库没有新增卡片

日期：2026-10-11（Asia/Shanghai）。

## 症状与根因

- 浏览器 localhost:3000 卡库显示 618 张，无搜索筛选，录入时间降序。
- 当天 61 个扫描任务全部 failed，无 collection_item_id；错误为 `column "revision" does not exist`。
- 当前 worker 在方向处理后递增 revision，本地数据库未应用 `db/migrations/0079_capture_sides.sql`。
- 修复数据库后，真实样本重试暴露第二个阻塞：模型返回 `redemption_status: null`，CardRecognitionResult 拒绝整个结果，所有视觉层级最终报失败。
- 单独调用真实 flash provider，复现上述 ValidationError，确认不是网络不可达。

## 修复

- 备份 scanner_recognition_job 到忽略目录 `.runtime/missing-scanner-revision/scanner-before.sql`。
- 核对 61 个任务的正反面原始及 raw 图片全部存在；现存任务满足迁移约束。
- 以本地表所有者事务应用已有 0079_capture_sides.sql，未改写既有卡库数据。
- 使用现有 normalize_provider_shape 将空兑换状态规范为 unknown，明确状态保留，非法状态仍拒绝。
- 重启 API 释放旧数据库查询计划并载入修复；恢复这批系统错误任务。重启中断的尝试退还一次计数；历史其他失败任务不重试。

## 验证

- 迁移前数据库语句稳定报缺列；迁移后同一 revision 更新在回滚事务中成功。
- 新增空兑换状态回归测试先失败：1 failed / 5 passed；修复后与 image_service、scanner_queue 测试合计 33 passed。
- 浏览器验证重新排队后显示“录入中”占位卡。
- 修复后真实 flash provider 成功解析同批样本，兑换状态为 unknown。
- 截至页面核验，4 张真实卡片成功入库、0 张再次失败；库从 618 增至 622，57 张仍待完成。浏览器点击空条件搜索后确认 622 张及新增 ID 627–630 可见。
- 列表任务接口默认只取最新 30 条，本批 61 条按最早任务先处理，因此最初完成的旧任务不会立即触发当前页面刷新；本次通过已有搜索刷新验证新卡可见，未修改分页与刷新行为。
- 剩余批次处理及识别字段准确率未在本次测试中完成评估。
- 未运行全仓测试及需要独立测试数据库的 capture PostgreSQL 套件。

## 后续核对：仍不可见的早前批次

- 当天批次最终为 17 张成功入库、44 张识别失败；总卡库 635 张。
- 18 点提交的 15 张全部失败，collection_item_id 均为空；00 点批次另有 13 张失败、01 点批次 16 张失败。
- 从 18 点批次取一个真实正反面样本，按当前配置分别调用 flash、plus、max；三个调用均返回 HTTP 403，错误码 `AccessDenied.Unpurchased`。
- 官方错误码文档将该错误归因为未开通百炼服务，需由 API Key 所属账号完成服务开通；不是已经确认的欠费错误。参考 https://help.aliyun.com/zh/model-studio/error-code 。
- 当前外部账号权限阻塞识别，未继续重复重试 44 个任务，也未将未知身份伪造入库。图片保留，待账号恢复调用权限后可重试。

## 充值后恢复

- 用户完成充值并明确要求重试入库；真实样本 flash 调用恢复成功。
- 备份任务表，保存精确的 44 个重试任务 ID；检查原始图片存在后重新排队。
- 处理期间 API 服务重启，4 个处理中任务被启动恢复逻辑标为 maximum attempts reached；仅对本批这 4 个未入库任务退还一次中断计数并重新排队。
- 最终数据库核验：重试的 44 个任务全部 succeeded，均关联存在且未删除的卡片，关联的 collection_item_id 去重后也是 44。
- 当天全部 61 个扫描任务均成功入库；无 queued、running 或 failed。未在此次恢复中评估所有识别字段的准确率。
