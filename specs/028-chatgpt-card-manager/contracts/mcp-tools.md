# MCP工具与授权合同

日期：2026-10-28。规划合同；下列接口尚未实现。

## 传输与认证

### T002部署参数

| 配置 | 首版值/约束 |
| --- | --- |
| SDK/OAuth协议组件 | `mcp==1.30.0`官方handlers及持久化provider |
| `CHATGPT_MCP_RESOURCE_URL` | `https://cardsp.cn/mcp`，同时为audience |
| `CHATGPT_OAUTH_ISSUER_URL` | `https://cardsp.cn/v1/chatgpt/oauth`，精确issuer |
| `CHATGPT_OAUTH_REDIRECT_URIS` | 账号实际回调的精确URI列表，禁止通配符 |
| 客户端 | 受限DCR/public，token auth `none`，不开放任意CIMD抓取 |
| PKCE/resource | 仅S256；授权和换token的resource须一致 |
| token时效 | access 900秒；refresh 30天；code/授权预请求5分钟 |
| 撤销 | refresh rotation/reuse detection，撤销整个连接 |

canonical URL由配置生成；不得信任请求Host作为issuer或批准地址。真实设备回调仍须验收。

- 稳定公网HTTPS `/mcp`，Streamable HTTP，复用FastAPI生命周期并验证SDK挂载路径，不能产生 `/mcp/mcp`。
- OAuth 2.1授权码 + PKCE S256；protected-resource/authorization-server metadata使用标准well-known发现；资源URL、issuer、audience、expiry、scope逐请求验证。
- 认证流程与现有登录连接，不让模型读取密码或token。采用成熟身份组件，选型和callback注册在T002/T005完成，不开放任意redirect URI。
- MCP资源端与既有API登录职责分离，撤销授权后所有工具及私有媒体访问立即失效。

## 统一类型与错误

- `McpPrincipal`、`PreparedOperation`、`ChatGPTFile`、`ResearchRun`、`BatchJob` 的字段约束见 [../data-model.md](../data-model.md)。
- 请求schema `extra=forbid`；不含owner_id。Python入口 `async def <tool>(principal: McpPrincipal, request: <ToolName>Request) -> <ToolName>Result`，PascalCase模型从工具名直接转换。
- 返回 `structuredContent` 为对应Result；同时用简短text返回数量/状态和需用户补充的信息。工具结果不得含凭证或内部任意路径。
- 错误 `AUTH_REQUIRED`、`FORBIDDEN`、`NOT_FOUND`（含他人资源）、`INVALID_INPUT`、`REVISION_CONFLICT`、`CONFIRMATION_REQUIRED`、`EXPIRED_OPERATION`、`IDEMPOTENCY_CONFLICT`、`SOURCE_BLOCKED`、`INSUFFICIENT_EVIDENCE`、`RETRY_EXHAUSTED`。提供可执行恢复动作，不把网络错误转换成空成功。
- 以下均为无默认写入授权的业务工具；只读设置 `readOnlyHint=true`，写工具准确标注；检索公网的工具 `openWorldHint=true`。annotations辅助客户端行为，不能替代服务器验证。

## 工具清单

| 名称 | 输入（除注明外必填） | 核心结果 | scope / 故事 |
| --- | --- | --- | --- |
| `get_capabilities` | 无 | schema_version、enabled_tools、limits | cards:read / US6 |
| `get_profile` | 无 | 账户显示名、账户ID、授权scope | cards:read / US6 |
| `search_cards` | filters可选、page=1、page_size=30（1–100）、sort_by/order可选 | items、total、page、next_page、sort | cards:read / US1 |
| `get_card_detail` | item_id | 身份、图引用、持仓、交易及估值来源/日期、revision | cards:read / US1 |
| `get_card_image` | item_id、side=front/back、variant=preview | 授权MCP image content或实测通过的媒体结果、revision | cards:read / US1 |
| `get_portfolio_summary` | 无 | 成本/估值/盈亏口径、币种、未估值数量 | cards:read / US1 |
| `prepare_card_import` | files、sides、idempotency_key | job_ids、batch_id可选、逐图映射、状态 | cards:write / US2 |
| `prepare_capture_confirmation` | job_id、corrected_fields、idempotency_key | operation_id、摘要、approval_url、expires_at | cards:write / US2 |
| `prepare_card_change` | item_id、expected_revision、changes、idempotency_key | PreparedOperation预览 | cards:write / US3 |
| `prepare_transaction` | item_id、expected_revision、transaction、idempotency_key | 卡片身份及交易影响预览 | transactions:write / US3 |
| `prepare_trash_change` | item_id、expected_revision、action=trash/restore、idempotency_key | 软删除/恢复预览 | cards:write / US3 |
| `execute_operation` | operation_id | outcome、resource_ids、readback、already_executed | 按操作kind重新校验scope / US2–US4 |
| `start_card_research` | item_id、expected_revision、idempotency_key | run_id、job_id、合同版本、sources计划 | research:run / US4 |
| `get_research_result` | run_id | 新旧研究标记、捕获/审核、来源缺口、价格可null | cards:read / US4 |
| `prepare_research_publication` | run_id、expected_revision、idempotency_key | 经服务端验证审核结果的发布预览，不允许自定price | research:run与cards:write / US4 |
| `create_batch` | kind=capture/research、item_refs（1–20）、idempotency_key | batch_id、逐项job_refs | 按kind校验cards:write/research:run / US5 |
| `list_jobs` | kind可选、cursor可选、limit=20（1–100） | 用户近期任务、批次及next_cursor | cards:read / US5 |
| `get_job_status` | job_id或batch_id（二选一） | 逐项状态、结果引用、错误、retryable | cards:read / US2/US5 |
| `retry_job` | job_id、idempotency_key | 原任务/新attempt、retry_count、状态 | 原任务要求的写scope / US5 |
| `get_activity_report` | start、end、timezone=Asia/Shanghai | 原币种买卖/费用/已实现盈亏及缺失数据 | cards:read / US5 |

filters复用现有 `SearchFilters`（含q、player、brand、product、autograph、numbered、grade等）；sort复用 `portfolio_sort.py`。未知过滤字段拒绝，不静默忽略。

## 附件与图片

`prepare_card_import` schema的顶层 `files` 为ChatGPTFile数组；`_meta["openai/fileParams"]=["files"]`。每项必须声明download_url、file_id、mime_type、file_name，仅前两项required，后两项可省略。`sides` 为等长数组 `{side, group_id}`，相同group_id只在明确正背对应时复用；auto表示进入既有单图/多卡识别。

URL和文件仅作为上传输入，不接受模型给出的本机路径。超时/附件过期提示重新选取；下载检查与限制见数据模型。重复内容只能提示潜在重复，不能把同款不同实卡自动合并。

私有图先测试授权后MCP image content，不把任意平台UUID当ChatGPT file_id，不把带Cookie的私有URL当公开图。若客户端不展示，记录差异并实现经验证的替代显示路径，不能跳过三端验收。

## prepare / approve / execute

prepare写入待确认操作，返回具体卡、完整参数、预期影响、有效期及平台确认页。已登录用户在 `POST /v1/chatgpt/operations/{id}/approve` 确认摘要（CSRF保护）；服务器核对owner、connection、hash和有效期，设置approved。该approve入口不注册成MCP工具。

`execute_operation` 只接受operation_id，不能改参数或用布尔值代替批准。执行时锁定operation及业务目标，重查权限/批准/版本；副作用和成功结果在同事务提交；重复执行返回原结果。接口 `GET /v1/chatgpt/operations/{id}` 供已登录用户查看，其他用户返回404。

更改身份复用既有审核链，不用通用patch直接绕过版本证据；交易复用 `PortfolioRepository.add_transaction`；入库复用 `CaptureService.confirm`。如果业务状态已变化，返回REVISION_CONFLICT并要求重新prepare。

## 研究与后台

研究worker每卡建立prepare包，实际访问两个市场，捕获响应与实卡图，逐候选审核后执行validate。CLI逻辑通过受控路径的服务适配使用，不接受任意shell/文件路径；若现有worker不满足双市场实际捕获门槛，必须补足后才算新研究完成。

研究审核记录保存在云端；工具读取审核和缺口，不让模型任填价格直接发布。需要人工核图时返回review_required及平台审核入口；审核未完成不能进入publish预览。

批次只引用已授权capture jobs或item IDs，不接收任意操作名。重试仅failed且retryable，最多3次；取消或失败不会删除已成功结果。结果不推送到其他聊天或设备，用户通过list/status主动查询；定时通知另有请求再设计。
