# Card Platform 开发约定

- 知识库迭代以云端为准；仅更新、发布和验证云端知识库，不同步更新本地运行知识库。本地采集与临时校验材料不作为知识库主库。（用户要求，2026-10-22）

开始工作时阅读 `.specify/memory/constitution.md`。工作流与调用方式见 `docs/ai-development-workflow.md`。

- 保留现有未提交修改；仅编辑当前任务涉及的文件。
- 新功能或跨模块改动使用 Spec Kit 维护一份 spec、plan、tasks；Superpowers 可执行同一份任务，不重复生成另一套需求和计划。
- 识别、裁切、正反面匹配等缺陷优先走复现、根因定位、最小修复、真实样本回归。根据任务使用 Superpowers 的 systematic-debugging、test-driven-development 和 verification-before-completion（插件可用时）。
- 小改动按需直接实现和验证，不强制走完整规格流程。
- 当前任务已明确授权的工作持续推进；仅在缺失关键需求或需要新的操作授权时询问。
- 未运行的测试、缺失的样本和仍不确定的识别字段必须明确报告。
- Codex逐卡估值必须实际搜索eBay及卡淘历史成交并留捕获；使用`docs/codex-valuation-research.md`及`scripts/codex_sales_research.py`的准备/校验流程。来源分列、核图/版本/品相/日期/真实金额/原币种/运费/重复实卡，证据不足价格null；不得重跑旧硬编码经验价导入。

- “部署到线上”默认指同时发布网页端和手机端，并同步部署所需的云端 API；手机端沿用当前个人 iPhone 开发签名渠道，覆盖安装并验证版本/启动，除非用户另行指定渠道。

- 自动成本 worker 在线上默认且持续开启（`PURCHASE_COST_WORKER_ENABLED=true`）；部署、重启、回滚均须保留开启状态，并验证运行容器实际设置为 true。除非用户明确要求暂停，不得沿用历史关闭配置或发布脚本中的 false 断言。测试环境关闭配置不可复制到生产。

- 线上视觉调用默认统一使用 `deepseek-flash`，请求必须显式携带 `thinking: {type: disabled}`（DeepSeek 默认会开启思考，不能用 Qwen 的 `enable_thinking=false` 代替）。网页、API、CLI、worker、回滚与后续发布都须保留这次迁移。独立 `qwen-plus` 联网搜索保留原百炼端点/密钥，通过 `SEARCH_API_KEY`、`SEARCH_BASE_URL` 与视觉分离；缺配置时不得把搜索密钥发到 DeepSeek。发布应合入已提交的视觉适配，不得从旧源码覆盖线上后仅沿用新环境变量。
