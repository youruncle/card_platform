# ChatGPT 卡牌管家 Implementation Plan

> **For agentic workers:** 使用 `superpowers:executing-plans` 按 `tasks.md` 顺序实施。沿用同一份 Spec Kit 规格、计划与清单，不另建计划。每项验证后记录证据再勾选。

**Goal:** 在手机和电脑 ChatGPT 中，用同一云端卡库完成查询、拍照入库、收藏管理、买卖记账、成交研究和批量汇总。

**Architecture:** 在现有 FastAPI 挂载远程 Streamable HTTP MCP，薄适配现有业务服务。OAuth 2.1 约束账户访问；Skill 定义流程，私人插件打包技能与连接。PostgreSQL及私有媒体继续作为事实来源，后台任务不依赖聊天存活。

**Tech Stack:** Python >=3.12、FastAPI、Pydantic、psycopg/PostgreSQL、官方 Python MCP SDK；Next.js 授权/确认页面；Markdown Skill及插件manifest。新增依赖在T002核查并固定兼容版本。

**Spec:** [spec.md](spec.md)

**Date:** 2026-10-28。实际Git分支仍为 `codex/cardhobby-valuation-quality`。实施按后续授权原目录进行，保留全部未提交规划及其他任务，不切换共享工作目录分支。功能标识028不代表Git分支。

## Global Constraints

- FR-001：所有入口共用云端主库，不用聊天记忆、本地运行知识库替代。
- FR-005：关键写入预览、确认、幂等、并发校验、回读；永久删除不开放。
- FR-006：逐操作鉴权、所有权及作用域校验；凭证不得进入技能、聊天或日志。
- FR-007：每卡真实搜索eBay、卡淘并留捕获，执行 `docs/codex-valuation-research.md` 和 `scripts/codex_sales_research.py` prepare/validate；证据不足价格 `null`。
- FR-010：本地安装不代表手机可用；私人分发、账号权限、附件路径先实测。
- FR-013：保留现有未提交修改；迁移实施时领取未占用编号，不批量改写历史卡库。
- FR-014：`PURCHASE_COST_WORKER_ENABLED=true`；视觉 `deepseek-flash`、显式 `thinking: {type: disabled}`；独立 `qwen-plus` 使用原百炼端点及 `SEARCH_API_KEY`/`SEARCH_BASE_URL`，禁止搜索密钥发往DeepSeek。
- FR-015：真实设备、图片、外部市场缺失时保持相关验收未完成。

## Summary

| 阶段 | 交付物 | 完成判据 | 任务 |
| --- | --- | --- | --- |
| 0 接入预检 | 账号、私人分发、手机附件验证记录 | 确定实际可行的跨端连接路径 | T001–T003 |
| 1 MCP与认证 | /mcp、OAuth、作用域、操作基础 | 未授权拒绝、可撤销、隔离、幂等 | T004–T010 |
| 2 查询卡库 | 搜索、详情、图片、持仓汇总 | 12条请求与云端同账户结果一致 | T011–T014 |
| 3 拍照入库 | 单面/配对/多图草稿及确认 | 真实样本回归，重试不重复入库 | T015–T019 |
| 4 收藏与记账 | 校正、买卖、费用、回收站/恢复 | 账本回读及并发/重复验证 | T020–T024 |
| 5 成交研究 | 双市场取证、审核与估值发布 | 两卡真实研究；证据不足null | T025–T029 |
| 6 批量与报表 | 批次、近期任务、部分重试、月报 | 断线恢复、逐笔月账一致 | T030–T033 |
| 7 Skill与插件 | 技能、manifest、安装及评估集 | 正确选工具，未交付能力不调用 | T034–T037 |
| 8 发布验收 | 云端、浏览器/iPhone/Android验收 | 三类设备实测及平台回读 | T038–T042 |

各阶段均在完整范围内；只读MVP不是最终交付。US6的可行性前置验证，完整业务验收后置。

## Technical Context

- **Language/Version:** 沿用API Python >=3.12及Web TypeScript。
- **Primary Dependencies:** 现有FastAPI/psycopg/Pydantic；新增官方mcp SDK与维护中的OAuth库，优先标准身份服务，不手写密码学。T002选择与现有账户结合的兼容方案。
- **Storage:** 既有PostgreSQL、capture/scanner jobs与私有媒体；只新增授权、操作及批次关系。
- **Testing:** pytest/pytest-asyncio、临时PostgreSQL、MCP Inspector、真实ChatGPT与设备。
- **Target Platform:** 云端Linux容器及ChatGPT网页/iOS/Android；不依赖本机常开。
- **Performance Goals:** 查询每页最多100条；耗时操作返回任务编号；不承诺未经测量的时延。
- **Scale/Scope:** 个人私人使用，6个用户故事，不公开上架或自动购买/付款。
- **External Dependencies:** 账号自定义连接权限、私人Skill云端分发、附件及图片显示路径。均为前置实测门槛，尚未验证。

## Constitution Check

| 原则 | 设计前 | 设计后 |
| --- | --- | --- |
| 架构与成果 | 通过 | 薄适配现有服务，不重写平台 |
| 识别可追溯 | 通过 | 原图、证据、人工确认、身份历史沿用 |
| 原始症状验证 | 通过 | 单/双/多卡图、重复、并发及跨设备回归 |
| 数据隔离 | 通过 | OAuth主体、作用域、owner、媒体及撤销校验 |
| 真实估值 | 通过 | 双市场捕获审核，null及缺口明确 |

无原则豁免；外部依赖仍须T001–T003实证。

## Project Structure

本目录为唯一规划源：`spec.md`、`plan.md`、`tasks.md`、`research.md`、`data-model.md`、`contracts/mcp-tools.md`、`quickstart.md`和质量清单。

下列代码路径均为规划目标，不表示已经创建：

| 路径 | 职责 |
| --- | --- |
| `apps/api/src/card_platform/mcp/server.py`、`schemas.py` | 生命周期、工具注册、能力版本及schema |
| 同目录 `auth.py`、`oauth_api.py` | OAuth资源验证、发现及授权，与现有登录连接 |
| 同目录 `operations.py`、`operation_api.py` | 参数预览、受信确认、幂等执行、审计及并发 |
| 同目录 `portfolio.py` | 搜索、详情和组合适配 |
| 同目录 `media.py`、`captures.py` | 附件接收、图片回传、CaptureService适配 |
| 同目录 `mutations.py` | 收藏校正、交易、软删除及恢复 |
| 同目录 `research.py` | 研究准备、捕获、校验及审核发布 |
| 同目录 `jobs.py`、`reports.py` | 批次、状态、失败项重试及账本聚合 |
| `apps/api/src/card_platform/main.py`、`config.py`、`apps/api/pyproject.toml` | 增量挂载、配置及依赖 |
| `apps/web/app/chatgpt/authorize/page.tsx` | 平台登录后的授权页面 |
| `apps/web/app/chatgpt/actions/[operationId]/page.tsx` | 绑定具体摘要的操作确认 |
| `apps/api/tests/test_mcp_*.py` | 分模块合同、认证、业务及集成测试 |
| `db/migrations/0088_chatgpt_manager.sql` | 暂定编号，实施前检查是否被占用 |
| `plugins/card-manager/plugin.json`、`mcp.json`、`skills/card-manager/` | 插件、远程依赖、技能及评估集 |
| `scripts/verify-chatgpt-manager.py`、`docs/chatgpt-card-manager.md` | 部署验证、使用与恢复说明 |

## Interfaces and execution

精确工具输入、输出、作用域及错误见 [contracts/mcp-tools.md](contracts/mcp-tools.md)，实体与约束见 [data-model.md](data-model.md)。

Python工具统一形态 `async def tool(principal: McpPrincipal, request: ToolRequest) -> ToolResult`，按合同定义具体请求与返回模型。owner仅来自已验证principal；不能由模型选择账户。

- OAuth采用authorization-code + PKCE S256、资源/授权服务器发现、resource/audience/scope验证。已有移动端Bearer session不能直接视为MCP OAuth token。
- 文件输入使用官方顶层fileParams声明，支持文件对象数组；远程URL先做SSRF、重定向、大小、时效及实际格式验证，凭证/临时下载URL不进入持久记录。
- 私有图片优先测试MCP image content回传；不把Cookie保护URL直接交给ChatGPT假定可读。临时授权展示地址仅在确有需要并验证后使用。
- 所有关键变更使用prepare/approve/execute。approve只能由平台已登录用户在绑定摘要页面完成；模型传 `confirmed=true` 无效。首版用原站确认页，后续可用受支持组件改善体验。
- 工具调用 `PortfolioRepository`、`CaptureService`、身份审核及估值服务；仅必要时抽取局部共享服务，不复制计算规则。
- 启动新逐卡研究和发布结果分开：启动按用户授权范围执行，结果严格验证/审核；接口可用不等于市场取证已完成。
- 私人MCP连接不需公开上架；附带Skill的跨设备分发另行实测，不能用本地marketplace代替。

## Review Focus

- 相似实卡误选：T012/T020核对稳定编号、图、限编与评级后再变更。
- 确认后摘要/目标变化：T007/T009/T021验证摘要绑定、过期及版本冲突。
- 附件过期、SSRF和图片膨胀：T015/T016验证下载与解码预算及错误。
- Sold字样但金额/付款/版本不明：T025/T028排除挂牌、未知BestOffer及重复实卡。
- 月末、空组合、异币种/缺汇率：T032使用半开区间及分币种统计，缺数据明确。

## Verification and handoff

行为实现采用必要的TDD：写有业务意义的失败用例、确认失败原因、最小实现、相关测试通过。文档与manifest排版不写镜像测试。每项完成在 `verification.md` 记录任务ID、代码版本、命令、真实结果、样本/捕获及未覆盖范围，再勾选任务。

[quickstart.md](quickstart.md) 是未来执行指南；尚不存在的测试/脚本不得宣称已运行。规划阶段仅验证文档结构、链接、依赖及覆盖。

当前开发进度14/42（2026-10-28）；正在按现有清单实施，未发布、未完成跨端验收。默认当前聊天逐项推进，不自动创建新聊天、定时任务或委派开发。后续“继续卡牌管家”先读本目录，从首个依赖已满足的未完成任务继续；仅缺账号操作权限、真实设备或关键外部配置时请求对应输入。

发布遵循仓库约定：所需API、网页与当前个人iPhone签名渠道覆盖安装/启动验证，保留数据；纯云端连接测试不得误报手机App已发布。
