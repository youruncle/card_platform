# Data Model
全部沿用既有实体，无迁移。Card owner 来自认证会话；CostCandidate 用 unit_id/evidence_hash 确认；IdentityReview 使用 current/suggested/candidates 和逐字段 provenance；交易使用 buy/sell/fee/adjustment、正 quantity、非负 price/fee、正 fx；回收站仅永久删除已软删除项。临时表单与选择状态限当前页面，密码不持久化。
