# Portfolio 联合估值合同 v2

fair_value/valuation_low/valuation_high/valuation_currency 表示当前有效派生值。valuation_synthesis.rule_version 为 codex-joint-evidence-2。此合同取代 v1 两份模型估价加权规则；API 不再发布该加权结果。

## 状态与审计
- joint_recalculated：新合格成交加入联合证据，成交估值器产生新的价格/区间/置信度。
- joint_validated：Codex 身份与原已用成交一致，无新增成交，保留原值。
- review_required：身份、引用金额或日期冲突，或原样本不能完整校验；保留原值。
- insufficient_evidence：没有原有效估值，也没有通过核验的成交。
- original_only：原值保留；人工估值不能自动覆盖。

审计包括 original_id/original_fair_value、codex_id/codex_analysis_version、recovered_evidence（原文、版本、位置、提取字段）、identity_checks、identity_corrections、joint_identity、accepted_evidence、rejected_evidence、confidence、fx、reasons。

## 证据规则
当前卡片ID、正反面URL及SHA256必须匹配历史；identity_confidence>=0.8、核心身份明确且无冲突。旧确认身份不能覆盖后来人工编辑的当前字段。来源明确 identity_confirmed、sale_confirmed、actual_price_known 后才能作为恢复成交；核验不超过90天、成交不超过365天。跨币种汇率需有来源，日期不超过7天。

原快照 used_comparables/comparables 引用的系统成交参与重校验；market_comp_ids 是召回池，不代表采用。缺失原样本不允许标成全部一致。明确非成交或未知实际价不能采用。同 URL 的金额/日期冲突进入复核；eBay 标题路径及追踪参数按商品ID归一，不能重复加权。

模型报价只展示原文对照。valuation_confidence 低或 reliable_price=false 不禁止该分析中的独立成交在补核后使用，也不能凭提高模型置信度创造成交证据。缺少 raw 身份、输入绑定等信息时明确保留原价，不伪造历史 identity_version_id 或 verified 标记。

图片复核修正仅影响当前估值上下文，按正反面哈希绑定，有时间和理由，且不覆盖人工确认。全部原始数据库记录保留。来源核验与汇率目前为本次任务的有日期记录，过期后须补核；列表读取不联网，也没有后台自动刷新任务。
