# Decisions
- Decision: 复用 bearer MobileApi 与现有 Web REST 契约。Rationale: 同一用户/业务规则。Alternative: 嵌入网页会损失原生会话与状态。
- Decision: 手机详情分区表单，批量只处理用户选择的卡片。Rationale: 小屏可操作且明确范围。Alternative: 原样复制桌面表格不适合触屏。
- Decision: 账号验证遵循现有官方 iframe 与消息校验，短信必须用户操作触发。Rationale: 不绕过验证，不自动发送。
