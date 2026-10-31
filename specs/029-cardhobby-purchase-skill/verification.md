# 验证记录（2026-10-29）

用户选“本人实际买入价”，并强调卡淘可按关键词查询。落地为关键词直接入口和实卡核图入口，两者共享云端账号/订单规则。原目录单人实施，无新worktree、委派、commit或真实账本写入。

## 代码与测试

- 首先3项RED复现缺preview_only能力；实现后唯一候选预览不调用confirm，原路径仍确认，预览不安排automatic_purchase_cost续写，3项通过。
- 新持久任务模块不存在RED；加入0089独立表及start/get/list/worker后，实际临时PG验证幂等、跨账号、同键异参、撤销前/搜索中阻止结果、并发只领取一次、过期租约三次停止、目标变化拒绝旧结果、关键词无需库存、分页/运费余分与错误脱敏。追加RED捕获同键旧结果不查revision、纯标点变空过滤、网络失败被误判账号登录，分别修复。
- Runtime工具列表先RED缺三个工具，接入后父API连续两次启动及完整工具发现通过。
- 最终相关命令：MCP_TEST_DATABASE_URL为独立manager_test_chatgpt_utf8，pytest test_mcp_*.py、test_cardhobby_account.py、test_purchase_cost.py、test_purchase_cost_search.py、test_purchase_cost_history_worker.py、test_purchase_cost_resume.py：**344 passed，23.88秒，1条既有Starlette弃用警告**。不代表全量API测试或全部旧PG成本测试通过。
- ruff及git diff --check通过。skill-creator初始化、quick_validate通过（用项目venv提供PyYAML，系统python缺该依赖未安装新包）。不进行无授权代理压力测试，真实聊天行为另验。

## 安装与发布

独立cardhobby-purchase-price-0.1.0.zip仅含两文件：SKILL.md、agents/openai.yaml，无密钥、真实订单、脚本或本机绝对路径。Chrome实际ChatGPT账号上传后显示“技能已上传”；关闭后“已安装”和“由我创建”均出现“卡淘买入价”。

发布前获取运行API四个既有源码与本地差异，确认main/account/service/server只差本次改动；基于原0.6.5-20261028-chatgpt3镜像覆盖这四文件及新mcp/purchase_cost.py，不覆盖其他运行源码。0089单独事务迁移及schema_migration标记，不应用无关0087。API切到0.6.5-20261029-purchase-skill1并healthy；成本worker=true、deepseek-flash显式禁思考、独立百炼搜索、OAuth等10项不变量全部通过；公网4项验证通过。实际连接get_capabilities已返回23个工具、包含三个新查价工具。重启切换时首次调用内部错误，健康后重试成功。

标准发布命令继续使用原compose.tunnel.yaml、原.env.cloud、028 compose.chatgpt.yaml，叠加029 release的compose.purchase.yaml；回滚仅恢复原镜像，保留worker=true及现有OAuth配置，新增表不删。网页/现有App无客户端代码改动，本次未覆盖安装iPhone二进制。真实手机端安装/调用尚未验收。

真实关键词查价及模型行为继续验证，不能把mock订单或安装成功当成实际卡淘搜索成功。

## 真实ChatGPT关键词查询与恢复（已执行）

Skill ID=6ae2312d9564819181434bced7999c33，实际账号“已安装”及“由我创建”列表可见。用户目标账号原MCP不变，未创建新OAuth授权、未扩大scopes。个人Codex的~/.codex/skills/cardhobby-purchase-price也已安装；当前根聊天工具目录未动态载入新工具，不据此宣称根聊天已可直接调用。

首轮真实Skill新聊天只选Skill后报告“当前连接未向本会话提供实际可调用的购买记录查询工具”，未创建任务或编造价格，记为能力缺失反例正确处理、正向查价失败。随后从原插件管理两次“刷新工具”；新聊天显式选择卡牌管家插件后可调用。私人聊天引用保存于git忽略验证目录。仅记录实测恢复路径，不推断ChatGPT内部缓存机制。

第二次真实聊天要求按指定中文关键词查本人买入价、不入账、不做估值。实际模型调用start_purchase_cost_lookup并回读get_purchase_cost_lookup，后台任务匿名为probe-A；首次running如实展示，未把排队当查价完成。生产DB只读核对确认该任务由实际客户端创建（未用脚本造任务），attempts=1，最终completed，result.source=live_private_order_keyword_search、search_complete=true、1条候选；原CardHobbyAccountService以prefer_live=True实际关键词请求，不使用archive快捷返回冒充联网。

跟进要求只读原任务、不得新建。模型回读商品标题、商品金额、运费、总成本、日期、商品链接与核验状态，均与生产任务持久记录一致，并明确关键词召回不能证明与某张实卡相同。搜索完成时间为上海2026-10-29 01:40:52；原编号恢复无新查价任务。真实金额/订单编号/商品链接及完整聊天引用仅保存在git忽略私有验证目录，不写入本规格或Skill。

第三轮自然语言请求仅提供英文关键词及“只查价、不入账”，没有指定工具名。模型自行调用对应查价流程并回读，01:47:52完成，搜索完整、命中同一购买记录，金额、运费、合计、日期与中文结果一致，并再次明确实卡身份未被关键词证明。实际正向关键词及跟进恢复通过；不把该有限样本推广为全库准确率。

主便携包更新为0.2.0含两项Skill，官方1.0.0 schema测试1 passed（0.42秒）；新增独立Skill包仍为0.1.0，仅两文件，另供artifacts/cardhobby-purchase-price下载。不宣称便携整包已云端导入；已安装的是独立Skill和原私人MCP。

## 手机验收边界与开发自查

已连接iPhone16 Plus/iOS26.6.1。devicectl默认只列developer apps，此前仅看到我的卡库1.1.17/build21，不能据此说没有ChatGPT；使用--include-default-apps --search ChatGPT后实际核对com.openai.chat 1.2026.153/build27158052172，与用户提供一致。未读取App容器、未改登录凭据或强制升级。iPhone内Skill发现/调用尚需真实客户端结果，Android设备仍未确认。已请求实际手机试用，T005保持未完成。

实现者单人自查：逐项确认关键词入口无需inventory、原卡淘客户端默认archive行为保持、只有preview分支不confirm/不安排自动续写、get/recent按owner且每次验证当前grant、搜索前后重查原grant、撤销后不保存可读结果、金额使用原Decimal分摊、分页保留所有候选并保留complete标记、错误不返回原异常/凭据。没有独立代理复审，遵循用户不委派要求；这弱于独立审查。未执行的手机验收与其他API全量测试不计通过。

### iPhone用户实测反馈（2026-10-29）

用户针对“在iPhone同账号新聊天选择卡牌管家/卡淘买入价，按关键词戴尔查询实际买入价”的验收请求明确回复“查到了”。记为iPhone真实客户端关键词查价用户实测通过，与前述电脑实测共同满足T005手机/电脑查询范围。证据类型为用户反馈，不是自动化手机UI观测；用户没有提供具体返回金额/任务编号，手机分页、金额逐项对照、撤销行为未独立验收。Android仍未测试。以上更新取代前文iPhone等待结果状态；不能关闭028的完整三端入库/记账等其他验收任务。
