# Data Model

自动任务：item_id主键及外键级联删除；owner_id用户外键；status queued/completed/failed；attempts 0..3；run_after、created_at、updated_at；result保存结果状态和安全原因。无需存储账号密码或订单原文。queued为可重试，completed为业务终态含无结果/需验证，failed为三次处理异常。
