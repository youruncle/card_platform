# 历史资料库验证

使用API虚拟环境和独立测试数据库运行成本、账号、worker相关测试。生产`PURCHASE_COST_WORKER_ENABLED=true`持续开启；测试fixture的false不得复制到生产。

运行`scripts/purchase_cost_history.py --phase sync|prepare|match --manifest <私有100例JSON> --output <私有目录>`。清单包含owner_id和不重复case_id/card_id；命令不接收凭据或硬编码金额。sync逐页续跑；prepare最多两个商品并行，每商品落盘，不持财务owner锁做OCR；match逐卡逐段调用正常服务并保存前后账本。

生产设置`PURCHASE_COST_ARCHIVE_DIR=/app/scanner-jobs/.purchase-cost-archive`，不要使用公开uploads。准备失败保留具体阶段与原文，`--retry-failed`明确重试；图组过期或订单修订会使相应进度失效。预算耗尽会保留checkpoint并报未完成。

实际恢复数量必须按同一100例的采购交易差集统计，区分原有成本、实际新增、待选择、证据不足、真实版本冲突和未完成；报告逐例状态及负例检查。发布只覆盖经审查的增量，保持其他发布及worker设置，核验公网、手机版本和启动。
