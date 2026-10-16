# 身份复核工作台未包含显式发现关联

状态：已复现，未在018发布中修改；无生产数据缺失或已确认决定缺失。

## 证据与影响

018账户卡库同步后，实际数据包36表73,178个源主键全部存在。保留共享市场证据原有canonical fingerprint后，原导出关系闭包少计20个证据ID；这20个ID均有本地显式discovery关联，涉及15张当前卡。

按应用MarketDataRepository.cached使用的binding→market_evidence_discovery_scope查询，20/20可达，查价/估值候选未丢失；按identity_review_api._evidence_rows当前查询，0/20可见。它只使用canonical fingerprint、definition_comp或当前decision，未归并discovery。源数据这20个关联没有任何该卡证据决定，均为未确认候选。3,849条账户证据决定的重要字段已核对一致；卡片、成本、既有估值和分析结果不受此候选列表限制影响。

## 后续修复边界

应以合成PostgreSQL样本先复现canonical fingerprint属于另一身份、当前卡存在显式discovery而无decision的情况，并验证其他账户和无关联证据不可见。

_evidence_rows同时参与确认流程，不能简单扩大查询后让全局classification赋予新身份接受权。修复需区分“发现候选”与“当前身份已接受证据”，确保新发现记录保持待复核；加入确认/撤销和其他身份分类不能沿用的回归后再发布。

018不修改该行为、不伪造decision、不覆盖共享市场分类。无正文只读聚合证据保存在受限运行目录，见018验收记录。
