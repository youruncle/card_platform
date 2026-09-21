# Card Platform 开发约定

开始工作时阅读 `.specify/memory/constitution.md`。工作流与调用方式见 `docs/ai-development-workflow.md`。

- 保留现有未提交修改；仅编辑当前任务涉及的文件。
- 新功能或跨模块改动使用 Spec Kit 维护一份 spec、plan、tasks；Superpowers 可执行同一份任务，不重复生成另一套需求和计划。
- 识别、裁切、正反面匹配等缺陷优先走复现、根因定位、最小修复、真实样本回归。根据任务使用 Superpowers 的 systematic-debugging、test-driven-development 和 verification-before-completion（插件可用时）。
- 小改动按需直接实现和验证，不强制走完整规格流程。
- 当前任务已明确授权的工作持续推进；仅在缺失关键需求或需要新的操作授权时询问。
- 未运行的测试、缺失的样本和仍不确定的识别字段必须明确报告。
