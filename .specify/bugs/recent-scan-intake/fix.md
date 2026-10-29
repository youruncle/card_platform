# 修复

- 视觉返回漏逐面结构时，最多一次同模型、当前两张图片补读；只补观察，不改联合身份。完整unknown/冲突不重读；补读失败保留草稿。
- 补读与原联合身份的球员、产品、卡号冲突时标记draft_identity_conflict，禁止自动入库；无通过门禁的捷径。
- 修复memorabilia_types空列表形状兼容，保留is_memorabilia未知。
- worker每60秒重新检查15分钟前失去更新的未入库running任务；达到最大次数也必须已过期才能标失败，保留attempt/started_at发布隔离。
- 模型路由保留入选结果的补读trace，token统计合并补读调用。非入选层的补读明细未展开，层级费用仍计入。
- 9个待核对任务由Codex逐图审核、精确版本/图片哈希约束，通过现有CaptureService.confirm恢复；失败/卡住任务使用正常worker重试。恢复脚本先预检，保存原模型结果，事务核对已有库存未变化。

无数据库迁移。生产镜像只覆盖image_service.py、scanner_queue.py、schemas.py、vision_routing.py；其余249个Python源逐SHA保持。网页与手机共用该API，无客户端源码变化。
