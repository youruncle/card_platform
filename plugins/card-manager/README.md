# 卡牌管家私人插件

便携包版本0.2.0；远程服务`https://cardsp.cn/mcp`，Streamable HTTP。包含卡牌管家及卡淘买入价两项Skill，无本地进程、密钥或卡库副本。2026-10-28已发布云端API/网页，2026-10-29新增关键词直接查本人买入价；实际账号已分别安装两项Skill。便携整包导入、手机端和完整业务验收仍待验证。

已实测的私人安装路径：ChatGPT Plugins→Add custom MCP server，填上述HTTPS地址并选择OAuth；通过原Card Platform账号登录及授权。随后进入“技能→添加技能→从电脑上传”，上传仅含本目录skills/card-manager下三份文件的技能ZIP，确认“技能已上传”及“已安装”列表显示卡牌管家。技能包保留card-manager/SKILL.md、card-manager/agents/openai.yaml、card-manager/references/workflows.md相对目录。在“在聊天中试用”时，将默认的“编写示例”提示替换为实际请求，避免模型改写掉筛选条件。原平台确认页在每次关键写入前展示具体影响。Chrome商店扩展不是该流程所需组件。

如账号另支持完整插件ZIP导入，可尝试本目录便携包并核对技能及远程服务器；该一体导入路径尚未实测，不等同于上述已验证的独立安装形态。

买入价Skill为`skills/cardhobby-purchase-price`，支持直接传1–3组关键词，不要求卡片先入库。新增start_purchase_cost_lookup/get_purchase_cost_lookup/list_purchase_cost_lookups三个远程工具，查询不落账；旧连接在插件管理中“刷新工具”后核对get_capabilities。安装包及常用请求见[买入价说明](../../docs/cardhobby-purchase-price-skill.md)。

服务器使用标准OAuth发现、受限DCR及public client PKCE S256。实际ChatGPT使用的回调URI必须先由部署配置精确允许；不接受通配符、聊天传token或固定Bearer替代授权。首次连接后执行get_profile/get_capabilities，核对账户与实际工具；撤销在平台的ChatGPT连接管理中进行。

本包使用公开Agent Plugins1.0.0的root plugin.json、mcp.json与skills目录。2026-10-28核查：OpenAI打包文档另描述server.extensions.com.openai.auth，但链接的1.0.0 mcp schema仍对server.additionalProperties=false且未包含extensions。本包保持标准schema有效的最小远程配置，在连接页明确选择OAuth并用服务器发现；是否自动导入OAuth仍须T037实测，未以本地marketplace代替云端安装。实际导入若要求认证扩展，保留当前标准包，按经实测的OpenAI导入格式另生成适配包并记录差异。

常用请求：“查我的库里签字卡”“核对这组正背照片，准备入库”“这张卡买入100元，费用5元，日期是……”“恢复上次三卡任务”“查看10月买卖和费用”。金额、日期、费用承担方和相似卡片不清楚时先补必要信息。照片先生成草稿，识别完成不等于入库；聊天中的“已确认”不能替代平台批准。

参考：[官方打包格式](https://developers.openai.com/plugins/build/plugins)、[上传格式](https://developers.openai.com/plugins/deploy/submission)、[官方自定义MCP连接步骤](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)。真实执行记录见仓库specs/028-chatgpt-card-manager/verification.md；设备和账号限制以实测为准。
