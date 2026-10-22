# 数据与边界

无数据库迁移。审计文件位于被忽略的 `.runtime/cost-audit-20261017/`，目录 700、文件 600。

- manifest：owner、固定种子、eligible_count、冻结 sample_ids、有序 ID 摘要与云端代码摘要。
- frozen：卡片身份、账户自己的原始购买订单、单件与占用、历史推测、知识版本。无密码、cookie、会话密文或联系人字段。
- inventory：读取范围、列表页计数、结构化订单/详情、列表与详情完整性分开、重试结果；不保存网页全文。
- replay：每卡查询计划（生成但未逐卡执行）、标准购买清单的召回商品行、身份冲突/缺失、订单错误、已占用件和分层状态。完整清单搜索没有使用关键词，不能把生成计划当已执行搜索。
- 投影：当前 card_number 的安全超集；全量保留非 card 词条与目录元数据、官方 artifacts；不改变匹配定义。
- 新候选 diagnostics：ready、identity_incomplete、identity_conflict、amount_blocked、allocation_blocked、scope。仅描述层次，不授权确认，不附加金额。

审计回放不是账本写入授权；text_candidate_ready 不等于已找回实际成本或实物唯一性证明。
