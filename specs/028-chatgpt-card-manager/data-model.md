# 数据与状态约束

日期：2026-10-28。以下为实施合同，尚未建立新表；收藏、交易、身份版本、估值及图片继续沿用现有实体。

## McpPrincipal / ConnectionGrant

- `owner_id: UUID`、`connection_id: UUID` 仅来自验证通过的OAuth上下文，不接受工具输入。
- `scopes: set[str]` 为 `cards:read`、`cards:write`、`transactions:write`、`research:run` 的子集；未知scope拒绝，写权限不隐含任意账户访问。
- issuer/resource/audience、expiry、revocation逐请求验证；authorization-code + PKCE `S256`，严格redirect URI白名单、state及CSRF校验。
- 授权/撤销绑定现有登录用户；access token短期有效，refresh rotation/reuse detection，凭证存储仅保留哈希或身份服务管理的安全引用。
- 现有Cookie/mobile session保持原用途，MCP token不无条件转为全权限session。

## PreparedOperation

- `operation_id: UUID`、`owner_id: UUID`、`connection_id: UUID`。
- `kind` 为 `import|update|transaction|trash|restore|publish_research`；输入字段按对应业务schema白名单。
- `payload` 为规范化后的确定参数；`payload_hash` 为其SHA256摘要，确认页只能批准这个摘要。
- `expected_revision: str` 为服务端产生的不透明快照，含相关身份/收藏/账本状态；不能只依赖可能未随交易变化的卡片updated_at。
- `idempotency_key: UUID`；唯一约束 `(owner_id, kind, idempotency_key)`。
- `expires_at` 为创建后15分钟；过期需重新prepare，不复用旧批准。
- 状态 `prepared -> approved -> executing -> succeeded|failed`；未执行的prepared/approved可变为expired/cancelled。执行失败是否可重试由错误类型决定。
- 批准必须来自现有平台用户登录后的确认入口，绑定owner、connection及payload_hash；模型传 `confirmed=true` 无效。
- `result_ref` 与执行成功在同一业务事务提交；同键同参返回原操作/结果，同键异参409。批准、执行和副作用使用数据库锁及唯一约束处理并发。
- 查询操作结果可在15分钟后继续回读成功记录；不能因幂等记录清理而重放已成功的写入。首版成功操作记录不自动清理。
- 审计保存操作类型、时间、主体、目标、摘要及结果引用；不保存OAuth token、附件下载URL和完整私有图片。

## ChatGPTFile / CaptureDraft

- 文件schema必须声明 `download_url: str`、`file_id: str`、`mime_type: str | None`、`file_name: str | None`，仅前两项required；fileParams指向顶层文件字段/数组。
- 首版 `files` 数量1–20；每项通过工具的 `sides` 显式映射 `front|back|auto` 和 `group_id`，长度必须与files一致；正背配对不靠附件顺序猜测。
- 单文件最大 `10 * 1024 * 1024` 字节，允许实际解码为JPEG/PNG/WebP；内容嗅探、像素/解码预算沿用既有媒体校验。
- `download_url` 仅用于受限下载：HTTPS、阻断私网/环回/保留地址、DNS及每次重定向检查、超时、流式字节限制，不把通用代理能力暴露给模型。
- 仅持久化owner、内容SHA256、受控私有路径及source file_id；短期URL不入日志或业务库。
- 草稿绑定capture job和revision；确认复用 `CaptureService.confirm` 的幂等/所有权及人工校正流程；新建收藏的owner由principal覆盖。

## Transaction / CardChange

- 复用 `TransactionCreate`：`quantity > 0`、`unit_price >= 0`、`fee_amount >= 0`、三字母币种、明确日期；金额Decimal，数量/金额精度沿用现有schema。
- 卖出不得超过实际持仓；日期按用户明确时区保存有时区时间；费用方向、币种、数量不明时不prepare。
- 外币交易缺可靠折算依据时不能默用1，要求补依据或返回不支持；统计保留原币种。
- 收藏patch只更新实际提供字段，避免 `holding_status` 默认值导致sold恢复为held；身份变更走既有审核而非直接改身份表。
- 软删除/恢复保留交易/身份历史；不提供永久删除工具。

## ResearchRun / ResearchEvidence

- `run_id: UUID`、owner、item_id、卡面哈希、身份版本、contract_version、开始/完成时间。
- 每来源记录query、attempted_at、状态、capture_ref、错误及恢复动作；来源为 `ebay|cardhobby`，没有结果与访问阻塞分开。
- 原文、实卡图、证据字段及审核严格复用 `docs/codex-valuation-research.md`，不另定更宽松门槛。
- 状态 `queued|running|review_required|completed|insufficient_evidence|blocked|failed`；两个市场未实际尝试或候选未审核时不得标completed。
- `price` 可null；研究草稿与发布估值分开，publish绑定审核结果、身份版本和原始捕获，不接受聊天直接指定无证据价格。

## BatchJob / ActivityReport

- `batch_id: UUID`、owner、kind（capture/research）、item refs；首版批次最多20项，先检查范围再启动。
- `idempotency_key: UUID`；同owner同键同参返回原批次，异参409。
- 子项分别 `queued|running|review_required|succeeded|failed|blocked|cancelled`；与既有任务状态显式映射，终态不混同。
- `retry_count` 首版最多3次；仅failed且retryable项可重试，不重跑succeeded，不把部分成功称全部成功。
- 报表区间 `start <= transacted_at < end`，默认 `timezone=Asia/Shanghai`；start必须早于end。
- 原币种小计、费用、买卖、已实现盈亏、未估值/缺汇率数量分列；报表金额来自账本聚合，不由模型求和。

## CapabilityVersion

- 已启用工具及schema版本由服务端只读返回；未完成/未启用能力不注册或明确disabled。
- 开发进度仅维护本目录 `tasks.md`；用户卡库任务由jobs工具查询。两者不互相冒充完成。
