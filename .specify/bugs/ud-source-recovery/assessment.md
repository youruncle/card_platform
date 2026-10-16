# UD失败来源恢复

2026-10-05；复用specs/009-official-checklist-knowledge，不另建需求计划。

基线v12：UD24候选，7发布，17失败。用户已授权修复、真实样本回归及自动发布。

根因：10个来源曾返回403（2022 Goodwin最新重试已200）；2018–2021 Goodwin浏览器导出达到约200k字符上限，带[Truncated]且table未闭合，下载器只检查文件存在，永远复用坏缓存；GR8 Moments官方article具有NHL license且46行均Alex Ovechkin或与Wayne Gretzky合卡，应排除，同步器原先会把excluded重新解析为failed；NSCC缺少运动列；World of Sports缺少年份与运动分类列。

最小修复：坏缓存保留校验值命名的隔离副本，重新获取并完整性检查后原子保存；403限速有界重试；按官方article的NHL许可排除并在同步流程保留排除状态；运动及年份仅采用可追溯官方证据，不猜测。只发布新通过校验的来源，保留已发布附件和其他草稿。

验证：先观察坏缓存替换、完整性、403恢复、NHL分类、同步排除状态的5项测试失败，再实现通过。后续进行实际下载、解析、附件审核、真实卡库回放及发布幂等检查。

进一步根因：2022-23 Metal Universe官方路径为/checklist/2022-metal-universe-champions/；2024-25官方路径为/checklist/2024-skybox-metal-universe-champions-checklist/，旧种子路径错误。官方brand目录已存档。修正URL但沿用既有source_key，队列和断点记录均归并canonical别名。NHL许可可能只覆盖混合产品的一部分（2021Metal含973条篮球），故非篮球排除严格限定于已核实的GR8，不能泛化所有NHL标记。

NSCC：仅为PC-2 Michael Jordan、VIPA-DF De’Aaron Fox补充逐行篮球证据，引用已取得的官方明确Category=Basketball行；绑定目标section/card/player，重新校验原始文件SHA256、源行定位与球员，保留原文和推导方法。其他80行继续隔离。

验证补充：从仓库根直接启动pytest未读取apps/api配置，已中断该运行（1179通过、20跳过、4失败，失败为test_confirmed_dual_player_memorabilia_card_is_not_rejected_as_a_lot、test_query_plan_uses_catalog_code_and_query_only_player_initials、test_query_budget_deduplicates_normalized_and_display_player_aliases、test_release_visual_parallel_query_survives_budget[KEVIN DURANT]）。随后从apps/api按项目配置完整重跑，2156通过、67跳过、1条Starlette弃用警告；最后补充canonical-only恢复分支专项测试11通过。未以中断运行作为验收。
