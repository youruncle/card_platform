# 接入研究与决策

日期：2026-10-28。已开始实施并核对登录账号的插件/Skill入口；尚未连接真实MCP、安装本插件或运行三端附件探针。

## T002：已固定的实现参数（2026-10-28）

- 官方Python SDK固定 `mcp==1.30.0`（Python>=3.10，兼容本机3.12.14）。采用维护中1.x线匹配FastMCP/Streamable HTTP接口，禁止无上界解析迁入2.x；升级须重跑协议/OAuth回归。OAuth直接复用SDK authorization/token/registration/revocation handlers、PKCE S256和TokenVerifier，不另造密码学或用户库。
- 已核实 [官方发行说明](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0) 和 [PyPI元数据](https://pypi.org/project/mcp/1.30.0/)。pip解析/安装通过，`pip check`输出 `No broken requirements found`；fastapi 0.139.2、starlette 1.3.1、pydantic 2.13.4。SDK wheel SHA256：`666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0`。
- Authlib 1.9.1也已验证兼容；SDK已提供所需OAuth协议，产品无需第二OAuth依赖。原计划“维护中的OAuth库”由同一SDK满足。
- resource/audience：`https://cardsp.cn/mcp`；issuer：`https://cardsp.cn/v1/chatgpt/oauth`，精确比较，不由请求Host推断。OAuth端点在issuer下 `/authorize`、`/token`、`/register`、`/revoke`；发现兼容根well-known路径与issuer子路径。
- 受限DCR公开客户端，token auth `none`，只允许authorization_code/refresh_token，PKCE只接受S256。redirect URI必须与部署白名单和注册值精确匹配。官方稳定回调为 `https://chatgpt.com/connector_platform_oauth_redirect`；账号管理页若给出callback-ID URI须登记完整URI，禁止通配符。SDK错误重定向未统一带iss，首版不宣称issuer-response支持，以账号实际回调为准。
- 原平台Cookie/mobile session只用于本人授权页面；同意后建立UUID连接，owner来自当前登录，scope白名单为 `cards:read/cards:write/transactions:write/research:run`。MCP token不能充当原API session。
- opaque access有效期15分钟，refresh有效期30天，仅存SHA256哈希；刷新旋转，旧refresh重放撤销整个连接及全部token；撤销逐工具/媒体请求生效。code和授权预请求有效期5分钟。
- 版本/参数确定不代表真实连接成功；ChatGPT账号授权、三端回调仍由后续真实验收覆盖。

## 决策1：Skill + 远程MCP，复用云端主库

**Decision:** 私人卡牌管家插件包含流程Skill及远程MCP连接。
**Rationale:** Skill规定工具顺序/输出/未知项处理；服务器提供实时私有数据、鉴权和受控写入。已有 `api.py`、`auth.py`、`capture_service.py`、`repository.py`可复用。
**Alternatives:** 仅Skill无法自行提供持续数据访问；仅MCP可快速验证连接，但不足以保证复杂流程一致，作为前置探针而非最终范围；本机脚本/本地marketplace不满足脱离本机使用目标。
**Source:** [Build skills](https://developers.openai.com/plugins/build/skills)、[Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)。

## 决策2：标准OAuth，不把现有Bearer session直接外发

**Decision:** 授权码 + PKCE S256、resource和发现机制、逐请求issuer/audience/expiry/scope校验；使用成熟身份服务/库与既有账户绑定。
**Rationale:** 当前 `auth.py` 支持Cookie/Bearer、`mobile_api.py`颁发session，但不是完整OAuth资源/授权服务。ChatGPT连接不能靠让用户在聊天粘贴自定义API key。
**Alternatives:** 复用移动session作为无限权限密钥不符合连接认证合同；全新用户库重复建设。具体兼容依赖在T002核查固定。
**Source:** [Authenticate users](https://developers.openai.com/plugins/build/auth)。

## 决策3：官方fileParams，附件路径先实测

**Decision:** 顶层files数组 + `_meta["openai/fileParams"]=["files"]`；schema声明download_url/file_id/mime_type/file_name，仅前两项required。
**Rationale:** 有正式文件传递协议，不能假定聊天图片自动成为任意工具参数；URL需时效、SSRF、重定向、大小/格式控制。
**Alternatives:** 手工复制照片URL体验差且不可靠；假设Cookie保护图可直接跨端显示未经证实。
**Source:** [Define file inputs](https://developers.openai.com/plugins/reference#define-file-inputs)、[File APIs](https://developers.openai.com/plugins/reference#file-apis)。

平台私有图片注册为ChatGPT工具结果file reference的完整结构未在本轮资料中证实。先实测MCP image content；不能把平台图片编号当ChatGPT file_id。需要组件时遵守资源域配置及 [Make images visible to the model](https://developers.openai.com/plugins/build/chatgpt-ui#make-images-visible-to-the-model)。

## 决策4：私人连接与Skill分发分别验证

**Decision:** 本人使用优先custom MCP connection，不公开上架；Skill与插件通过账号可访问的私人/工作区渠道安装。
**Rationale:** 官方说明插件支持网页、桌面及手机，但账号/工作区权限及本地插件分发不同。本机安装不能证明手机可用。
**Alternatives:** 公开目录需要额外上架流程且超出个人使用范围；仅本地marketplace不能满足目标。
**Source:** [Plugins](https://learn.chatgpt.com/docs/plugins)、[Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt)、[Private versus public submission](https://developers.openai.com/plugins/deploy/app-review#prepare-remote-mcp-capabilities-for-plugin-submission)、[Publish a local plugin to your workspace](https://developers.openai.com/plugins/build/plugins#publish-a-local-plugin-to-your-workspace)、[Skill distribution](https://learn.chatgpt.com/docs/enterprise/skills#skill-distribution-and-administration)。

## 决策5：操作幂等与确认基础先建设

**Decision:** prepare/approve/execute，确认页绑定具体摘要，结果事务提交；owner由认证上下文取得。
**Rationale:** `CaptureService.enqueue/confirm`已有幂等基础；`PortfolioRepository.add_transaction`有持仓锁，但新MCP交易需要补请求幂等与统一回读。`PortfolioItemQuickUpdate`有held默认值，部分patch必须避免意外重置状态。
**Alternatives:** 仅Skill口头要求不防止重试/并发；模型 `confirmed=true` 不证明平台用户批准。首版用既有站点确认页，嵌入式UI可后续优化。

## 尚待实际账号/设备验证的外部条件

| 条件 | 当前状态 | 恢复动作/任务 |
| --- | --- | --- |
| 自定义MCP连接和相应写工具权限 | 未实测 | T001记录账号入口、允许scope及限制 |
| 私人Skill在网页和手机可安装调用 | 未实测 | T001测试真实分发；缺入口记录所需权限，不算完成 |
| 单面、正背、多附件映射及私有图片显示 | 未实测 | T003分别测试浏览器/iPhone/Android并保存无敏感信息记录 |
| OAuth服务与现有账户可兼容 | 未选定版本 | T002选型，T005实际授权/撤销/刷新测试 |
| 两市场实际取证可达性 | 本任务未访问 | T028真实尝试，来源阻塞留capture和恢复动作 |

这些是有任务承接的外部验收门槛，不是已解决的能力或用户需要重新决定的需求。开发计划可完成，功能验收不能跨过它们。
