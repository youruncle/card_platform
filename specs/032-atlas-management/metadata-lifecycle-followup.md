# 首版纠错审核收尾：依赖核对与最小方案

基线b5a1a4c5；继续同一隔离工作树。不新增生产schema，不改原始来源、收藏、账本、实卡或价格记录。

## 代码核对
- 筛选/相关版本：KnowledgeBrowser.cards 的SQLite列 season/season_end/player/card_number/section/parallel 来自有效卡字段；身份筛选atlas_state来自atlas.identity_status。serial_total/is_autograph不作为SQL筛选参数，但参与身份完整性、显示与来源匹配。
- 来源：knowledge_atlas.reviewed_external_mapping 消费有效年份、球员、卡号、产品、版本/section、总编。任何身份覆盖后旧图绑定在派生缓存解除；source_key/checksum/variant/artifact hash和原始source断言保留。
- 价格：player_prices.discovery.card_key 对球员、年份、产品/品牌、子集、卡号、平行、总编/print_run、签字等分组。现有KnowledgeResolver/ScopedCardCatalog仅消费原entities/official_artifacts，不消费atlas_corrections；本首版覆盖仅作用于浏览派生目录，不自动改变存量价格分组/实卡。界面与API必须明确“未自动重算”，不能暗示同步。
- 症状：apply_corrections 更新卡字段但保留原atlas.serial_status/autograph_status；清空总编或改变签字/子集后会形成矛盾元数据。修复将有效派生状态按当前字段保守重算；source_atlas保存独立标记的原始来源状态。未知总编不猜成无编号，图鉴签字属性不等于实卡鉴真。

## 最小实施
1. 在同一纠错派生层补有效serial/autograph元数据、scope和correction_effects。原始身份/来源状态单列；链式覆盖不覆盖原始凭据。失败测试覆盖known→unknown、签字true/false/null、section变更、链式覆盖和源证据hash不变。
2. 复用已有knowledge_version快照和knowledge_audit_log，按提案页一次读取事件和版本成员关系：待审/待发布/过期待重新提案/当前发布包含/回滚移除。时间线保留全部提交、批准/驳回、首次发布、后续携带、回滚移除/恢复，以及版本号/ID、操作者、时间和理由。状态只表示快照包含关系，不冒充冲突覆盖已经有效。新相关审计事件使用真实插入时间，历史证据不改。
3. 网页显示有效派生状态和原始来源断言的边界、价格分组未重算；提案列表与详情共用发布状态标签，并显示时间线。服务器继续拒绝改变已发布审核历史。
4. 定向TDD→相关回归/类型/构建→真实浏览器关键流程→一次仅针对上述两项的独立复核。整合仍等待另一任务的实际最终读取适配文件/索引/缓存版本。
