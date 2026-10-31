# 实施验证与验收指南

日期：2026-10-28。**这是未来实施时使用的指南，以下新测试和脚本当前尚不存在，不能据此宣称已通过。** 当前进度见 [tasks.md](tasks.md)。

## 前置条件

- 读 [spec.md](spec.md)、[plan.md](plan.md)、[contracts/mcp-tools.md](contracts/mcp-tools.md)、项目constitution和AGENTS约定。
- 查看Git差异，保留现有未提交内容；实施使用隔离分支/已有适合worktree，不自动改写当前其他任务。
- T001–T003确认实际ChatGPT账号权限、私人MCP/Skill分发、附件及三类设备支持；没有权限或设备不以文档推断替代验收。
- 使用独立临时PostgreSQL、测试账号及测试卡做破坏性/并发测试；真实卡只在用户明确授权范围内写入。
- 按T002固定SDK/认证依赖，通过安全配置注入凭证。文档、日志、capture及Git不存明文密钥。

## 本地相关测试（对应任务创建文件后运行）

仓库根目录执行；先按各任务运行单个测试模块，新增行为确认RED后实现，再确认GREEN。

```bash
.venv/bin/python -m pytest apps/api/tests/test_mcp_auth.py apps/api/tests/test_mcp_operations.py -q
.venv/bin/python -m pytest apps/api/tests/test_mcp_portfolio.py apps/api/tests/test_mcp_media.py -q
.venv/bin/python -m pytest apps/api/tests/test_mcp_capture_files.py apps/api/tests/test_mcp_captures.py -q
.venv/bin/python -m pytest apps/api/tests/test_mcp_mutations.py apps/api/tests/test_mcp_trash.py -q
.venv/bin/python -m pytest apps/api/tests/test_mcp_research.py apps/api/tests/test_mcp_research_publication.py -q
.venv/bin/python -m pytest apps/api/tests/test_mcp_jobs.py apps/api/tests/test_mcp_reports.py -q
```

每个命令预期相关断言全部通过；数据库集成测试使用临时实例，记录连接来源但不输出密码。原有auth/capture/portfolio/valuation相关回归由实际改动选择，完整e2e仅在相应范围需要时执行。

## MCP与OAuth检查

```bash
npx @modelcontextprotocol/inspector
```

选择Streamable HTTP，输入实际环境的HTTPS `/mcp` 地址。核对initialize、tools/list、输入输出、annotations和能力版本；未授权/过期/错误resource/scope必须失败。完成用户登录授权、刷新、撤销、重新连接和其他账户资源拒绝；不要在截图或复制日志中暴露token。

## 真实业务场景

1. 按SC-001准备12条固定查询及已知结果，在ChatGPT和网页核对同账户item IDs、排序、持仓与汇总口径。
2. 单面/正背/三张独立卡/多卡图各一组，另加错配反例；核对原图、字段证据和不确定项；确认入库，三次重复提交仅一份结果。
3. 测试卡六种变更（买入/卖出/费用/校正/软删除/恢复），批准前后比较明确摘要，重复/并发请求不多账；外币缺可靠折算时不默认1。
4. 两张真实卡从当前正背图建立身份，实际访问两个市场、保存捕获、审核全部候选、prepare/validate和批准发布；一张覆盖证据不足null。已有结果阅读不算新研究。
5. 三项批次包含一项失败，断开聊天后查询恢复，仅重试failed且retryable，第四次重试拒绝；固定月份按Asia/Shanghai半开边界逐笔核对。
6. 开新聊天安装/调用完整Skill插件，直接/间接/缺信息/负例按照评估集执行，记录调用工具与输出。

研究准备/校验命令采用 `docs/codex-valuation-research.md` 的真实参数及私有输出目录，避免把临时材料误当知识库主库。来源失败必须有实际尝试记录；校验结构通过后仍需原始证据审核。

## 云端与设备验收

- 部署按现有安全发布流程，仅更新所需服务与配置。验证health/ready、MCP/OAuth发现、原登录和私有媒体；容器实际读取worker为true，视觉thinking disabled及搜索端点/密钥隔离。
- 网页与现有个人iPhone开发签名渠道的App覆盖安装后检查版本/启动与数据保留。发布脚本不得继承旧false断言。
- 电脑浏览器、iPhone、Android分别记录系统和ChatGPT版本、私人安装路径、查询、照片入库、记账、批次恢复、撤销后访问拒绝；核对平台回读。
- 缺Android设备、账号入口或真实市场捕获时，相关项保持未完成；可提供已完成部分和下一恢复动作。

## 记录与闭环

每项证据追加到 [verification.md](verification.md)，然后勾选任务。保存代码/插件/schema版本、命令/实际结果、脱敏截图或私有capture引用、样本数量及未覆盖范围。开发任务清单与业务job状态分别核对，不凭聊天中的“已完成”替代数据库结果。
