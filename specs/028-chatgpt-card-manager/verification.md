# 规划校验与后续实施记录

## 2026-10-28 实施启动：T001/T003（待外部输入）

- 基线：`635c2843`，原目录及分支 `codex/cardhobby-valuation-quality`，未切换/清理其他任务；初始差异清单保存在私有 `.superpowers/sdd/028-chatgpt-card-manager/initial-status.txt`。本任务仅增量改动相关文件。
- 已完整读取028规格/计划/任务/研究/数据/工具合同/quickstart及本记录。沿用42项任务，不重复规划，不委派开发。
- 实测：`cua.getState()` 显示本聊天无已登录浏览器标签页；IAB打开 `https://chatgpt.com/plugins` 30秒超时。通过原生Chrome新标签页访问成功，页面显示“登录”“免费注册”及公开插件目录，当前未登录，无法核实本人自定义MCP/写工具/私人Skill权限。
- 已通过异步问题请求用户在已打开插件页登录，并提供iPhone/Android及ChatGPT版本；不索取密码/token。登录后恢复检查“+ → Add custom MCP server”、私人插件管理及Skill分发入口，不能据公开目录或本机Codex插件推断可用。
- 浏览器：macOS本机Chrome实际打开页面，但ChatGPT业务调用未测。iPhone、Android：设备/系统/ChatGPT版本尚缺，未测。没有创建公开探针入口或读取/写入真实卡库。
- T001/T003保持未勾选；依据原任务依赖说明继续T002及隔离本地基础。三端files（单面/正背/三附件）与私有图显示仍未测。
- 基线命令纠正：首次引用不存在的 `test_private_media.py`，未运行测试；随后 `.venv/bin/python -m pytest apps/api/tests/test_auth.py -q` 实际为 **5 passed**。不将首次空运行计为通过。


日期：2026-10-28。

> 2026-10-28实施检查点：10/42（T002、T004–T012）。本任务相关回归90 passed；全量API运行中断且有失败，不是全库通过。未部署、未写真实收藏、未完成ChatGPT三端验收。下个实现入口：T013的客户端展示探针与T015–T018照片草稿/确认链。

## 规划启动时事实（历史快照）

- 已读取项目constitution、AI开发工作流和相关代码，保留现有未提交修改。
- 已核查官方Skill/MCP/插件、OAuth、文件输入和私人分发文档，来源及未证实事项见 [research.md](research.md)。
- 已建立一份spec、plan、42项tasks及数据/工具合同；没有创建产品代码、安装新依赖、部署服务或写入真实卡库。
- 未实际连接用户ChatGPT账号，未验证私人Skill跨设备分发，未运行附件探针、OAuth或设备测试。
- 未为本功能检索任何卡片成交；研究合同的代码及真实市场验收仍为待办。
- 开发/接入任务0/42；规划校验结果以本次命令输出及后续追加记录为准。

## 本次规划校验

- 用Python pathlib/re/json进行一次性文档检查：9个Markdown文件、T001–T042连续且全部未勾选；各任务有路径，US1–US6数量分别为4/5/5/5/4/4。
- 本地Markdown链接、代码围栏、尾部空白、FR-001–015与SC-001–007覆盖及Spec Kit功能指针全部通过，最终输出 `status=PASS, errors=[]`。
- `git diff --check` 无错误；现有跟踪文件修改仍是原有knowledge_release和009计划/任务，本次新增内容仅028目录及被Git忽略的Spec Kit活动功能指针。
- 未运行产品单测/端到端、未部署；本次是规划交付，不能视为MCP、Skill或跨设备功能可用。

## 任务完成记录格式

每条按以下字段追加；仅真正完成才将tasks对应复选框改为x。

| 字段 | 要求 |
| --- | --- |
| 任务ID/日期 | T编号与实际执行日期 |
| 代码/插件版本 | commit或可追溯差异及schema版本 |
| 命令/操作 | 可重放的测试命令或明确UI操作，不能泄露凭证 |
| 实际结果 | PASS/FAIL/部分通过及关键断言 |
| 样本/捕获 | 数量、类型、私有引用及必要哈希，不提交敏感图 |
| 覆盖范围 | 实际账号/设备/来源，未测和未知明确 |
| 恢复动作 | 外部限制、所需输入和下一步，不将限制写成成功 |

## 外部条件登记

| 事项 | 状态 | 下一任务 |
| --- | --- | --- |
| 自定义MCP及写工具权限 | 未实测 | T001 |
| 私人插件/Skill的网页与手机分发 | 未实测 | T001/T037 |
| 手机图片传入及私有图显示 | 未实测 | T003 |
| OAuth服务版本、回调及授权撤销 | 未实施 | T002/T005 |
| 双市场真实取证与审核 | 未执行 | T028 |
| 电脑/iPhone/Android全流程 | 未执行 | T040 |

## T002/T004：依赖与协议基础（2026-10-28）

- T002：官方SDK/OAuth组件及canonical参数已记录research和contracts；Python3.12.14，mcp1.30.0，pip check无依赖错误。Authlib1.9.1经过解析但不加入产品依赖；SDK已提供OAuth协议处理器。
- T004差异：新增mcp/server.py、schemas.py、__init__.py和test_mcp_server.py；main.py挂载SDK已定义的/mcp，复用父API生命周期及数据库，不创建第二worker。
- RED：`python -m pytest apps/api/tests/test_mcp_server.py -q` 4 failed，均因缺mcp模块。首轮实现后3项失败421；根因是SDK默认localhost:*不匹配不带端口Host，加入精确localhost/127.0.0.1/cardsp.cn白名单，仍开启DNS重绑定保护。
- GREEN：`.venv/bin/python -m pytest apps/api/tests/test_mcp_server.py apps/api/tests/test_auth.py apps/api/tests/test_capture_media.py -q` → **30 passed**（1条既有Starlette TestClient httpx弃用警告）。覆盖initialize/tools/list、未实现工具拒绝、错误不含内部路径、重复生命周期、恶意Host、原auth/media回归。日志在私有`.superpowers/sdd/028-chatgpt-card-manager/t004-{red,green}.log`。
- T004目前没有业务工具，tools/list为空；认证由T005接入后才能注册私有能力。未部署、未连接真实卡库或调用模型，真实附件/客户端尚未测。

## T001补充：登录后账号入口实测

- 用户回复已登录后，Chrome `https://chatgpt.com/plugins` 实际出现“公开/个人”及“添加”菜单，菜单含“创建插件”“上传插件压缩包”“添加自定义MCP服务器”。打开MCP表单，看到服务器URL/隧道、OAuth及风险提示；未提交创建或授权。
- `https://chatgpt.com/skills` 实际显示已安装技能与“添加技能”，菜单含“通过聊天创建”“使用编辑器创建”“从电脑上传”。这是账号云端入口可见的证据，不代表上传后的手机分发通过。
- 写工具实际调用、iPhone/Android设备及客户端版本仍未测，T001继续待办；可用入口已确定，待T003探针/真实连接恢复。

## T005–T010：认证、存储、确认与可靠操作（2026-10-28）

- 差异版本：028.1（当前工作区）。OAuth用官方SDK标准handlers，复用app_user；opaque token/code只存SHA256，owner/connection为UUID；MCP不接受原平台session代替OAuth。默认redirect白名单为空，真实回调未登记前拒绝注册。
- 独立PostgreSQL16通过本机initdb/pg_ctl启动在127.0.0.1:55438，测试库manager_test_chatgpt_utf8，应用0001–0086基线及0088新迁移；未应用其他任务未提交的0087。首次测试库因no-locale默认SQL_ASCII无法读取中文迁移，另建UTF8临时库后正常；未触及本地运行卡库/线上库。
- T005 RED：缺OAuth模块/0088迁移；之后真实HTTP用例捕获两处SDK边界问题：metadata默认只声明client_secret认证、公有客户端revoke要求显式nullable client_secret。适配层修正metadata并标准化撤销输入，核心协议处理仍来自SDK。9项认证测试GREEN，覆盖错误PKCE/resource/redirect/issuer/过期、code单次使用、scope、refresh旋转/重放整连接撤销、客户端白名单、登录会话/CSRF绑定。
- T006：迁移重放两次、scope约束、跨owner连接外键、幂等唯一约束通过。新增principal测试先观察到未知admin scope未拒绝（1 failed/2 passed），加入白名单验证后通过。仅新增授权/操作/批次/审计表，不另建收藏主库。
- T007/T009 RED：缺operations模块。GREEN 7项真实PostgreSQL测试覆盖同键同参回原操作、异参冲突、连接/owner绑定、15分钟过期、摘要篡改、未批准拒绝、3次并发仅一次写入、业务异常完整回滚、确认后revision及scope冲突、成功结果过期后仍可回读。handler接口显式复用同一事务，真实收藏/身份/账本快照适配随业务任务补齐，当前为计划要求的测试handler。
- T008 RED：缺operation_api。GREEN测试覆盖未登录401、其他owner404、缺失/错误Origin拒绝、额外confirmed字段422、摘要变化409、连接撤销401。approve只属于原平台登录API，不注册MCP工具。Next.js授权页/确认页已新增，`pnpm -C apps/web exec tsc --noEmit`退出0；尚未做真实浏览器业务验收。
- T010 RED：已认证tools/list仍为空。GREEN通过真正Streamable HTTP接口测试capabilities/profile及scopes、annotations、owner参数拒绝、token不进入错误、撤销后401。未实现业务工具不注册；audit仅写工具名、主体、摘要/结果码，异常日志仅类型不写参数/短期URL/凭证。
- 本轮总命令：`MCP_TEST_DATABASE_URL=<disposable> .venv/bin/python -m pytest apps/api/tests/test_mcp_*.py -q` → **26 passed，1条Starlette已有弃用警告**；之前加原auth/media回归42 passed。本轮测试日志私有保存在`.superpowers/sdd/028-chatgpt-card-manager/`，不含生产token。
- 仍未部署、未在ChatGPT连接调用、未做手机授权/图片探针。T001/T003/T014及所有真实设备验收继续未完成；不宣称阶段0或完整目标完成。

## T011/T012与T013/T015/T016部分实现（2026-10-28）

- T011/T012已实现owner-scoped查询、详情及组合，复用SearchFilters、search_item_ids、sorted_page和PortfolioRepository。实际运行12条具明确item IDs的查询，另覆盖分页排序、同款两张实卡、越权详情、空组合、未知过滤/分页限制及账本变化影响revision。原币种估值分列，未知或异币种折算缺失时total_market_value=null，沿用账本成本/已实现收益，不调用原网页成本暂估总值作为市场值。
- 初轮测试两处预期错误已按仓库真实合同校正：display_id从0起，不能用truthiness判断有效；cost过滤按账本基础币种，100 USD×7的已卖卡也满足cost_min=250。金额断言改用Decimal比较，不依赖显示小数位数。未修改原搜索或账本口径来迎合错误预期。
- 额外并发测试真实复现：等待实卡行锁的同一SQL中的账本子查询仍读取等待前MVCC快照。`ledger-lock-red.log`观察到旧/新revision相同；改为先取得行锁、下一语句读取完整实卡/定义/身份版本/账本/估值快照后GREEN，防止确认后的并发交易漏检。
- T013代码：抽取原capture_media_api的portfolio_image_path供HTTP与MCP共享，保持owner、软删除、revision、路径/符号链接检查及既有图片派生缓存。MCP返回可解码ImageContent和不透明revision，不返回磁盘路径、Cookie URL或假file_id；图处理前后重查连接/卡片版本，撤销后拒绝。测试含真实合成JPEG（32×48）解码、他人图片、缺背面、撤销和错误side；真实ChatGPT是否显示仍待T003，故T013暂不关闭。
- T015/T016代码：文件schema仅download_url/file_id required，mime_type/file_name可省略，files/sides等长1–20；同group仅允许明确正背配对。受限下载只允许HTTPS443、阻断私网/环回/保留/IPv6转换地址，检查全部DNS答案及每次重定向；固定数值IP连接并用原域名验证TLS SNI，禁止环境代理/自动重定向/压缩膨胀，30秒总预算、10MiB流式预算、沿用40MP和JPEG/PNG/WebP实际解码。
- 实际下载边界19项通过（包含MockTransport模拟外部HTTP/DNS，非真实ChatGPT附件）：固定IP/SNI、混合公私DNS、逐跳私网重定向、过期403、头部/流式超限、格式错误与signed URL不进入httpx/httpcore日志。参考官方HTTPX sni_hostname扩展： https://www.python-httpx.org/advanced/extensions/ 。短期URL尚未持久化；保存/同键异图/后台capture适配由T017继续，T015/T016保持待办，不能据下载模块测试声称录卡可用。
- 父API新增生命周期重启集成测试发现SDK同一session manager不可再次run（RED RuntimeError）；现每次父lifespan创建新MCP运行时，真实TestClient连续两次启动、ready和MCP未授权401通过。OAuth请求新增64KiB body预算，防止自定义SDK边界丢失有界读取。
- 本轮相关测试命令（含原auth/media）实际 **88 passed，1条已有Starlette弃用警告**；之后生命周期新增用例 **1 passed**。ruff仅检查本任务新模块/测试，All checks passed；Web TypeScript检查退出0。完整API回归另行运行，结果待追加，不能提前视为通过。
- 当前已勾选10/42（T002、T004–T012），T001/T003/T013–T042继续待办。没有发布、写入真实收藏、运行模型识别或双市场成交研究。

## T001设备库存补充（2026-10-28）

- macOS Chrome版本156.0.8078.48（读取本机Info.plist）；真实网页账号已登录并核对入口。
- `xcrun devicectl`首次因默认Developer目录未指向Xcode而不可用；按当前命令设置DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer后成功列出设备。脱敏结果：一台已连接iPhone17,4/iOS26.6.1，另两台iOS27.0离线。私有设备标识仅存git忽略的临时目录，不进入本报告。
- 已连接手机的应用库存找到cn.cardsp.alpha.local“我的卡库”1.1.16/build20，未找到OpenAI ChatGPT。已请求用户安装/登录官方ChatGPT并提供版本，以及Android可验收设备。没有安装/删除/启动手机App，没有修改现有数据；安装库存不算ChatGPT业务验收。

### 用户补充（2026-10-28）

用户说明其iPhone已安装GPT但版本较旧。上述devicectl清单只代表当前连接设备的可见应用库存，不能据“未列出”断言用户手机未安装。后续优先以现有版本实测私人插件/附件；实际设备是否与连接清单一致、ChatGPT版本号及入口仍待核验，缺功能时再确认是否需升级。Android设备仍未确认。

## 本轮收束验证与回归边界（2026-10-28）

- 最终相关命令：`MCP_TEST_DATABASE_URL=<isolated manager_test_chatgpt_utf8> .venv/bin/python -m pytest apps/api/tests/test_mcp_*.py apps/api/tests/test_auth.py apps/api/tests/test_capture_media.py -q` → **90 passed，1条Starlette TestClient既有弃用警告，5.47秒**。包含真正父API的连续启动、OAuth两类发现路径、六个实际启用工具列表与授权后空库查询；不仅是独立SDK小应用的测试。
- `.venv/bin/python -m ruff check apps/api/src/card_platform/mcp apps/api/tests/test_mcp_*.py apps/api/tests/conftest.py` → All checks passed。`pnpm -C apps/web exec tsc --noEmit` → 退出0。`git diff --check` → 退出0。`pip check` → No broken requirements found。
- 尝试全量 `.venv/bin/python -m pytest apps/api/tests -q`，另行collect确认159,380项，其中155,062项属于test_shared_player_aliases_and_rejection_audit.py。全量在182.70秒中断，已观察 **123 failed、1392 passed、94 skipped**，未运行余下用例；不能宣称全量通过。123项名称完整记录在 [api-suite-observed-failures.txt](api-suite-observed-failures.txt)，原始失败输出仅保存在git忽略的私有目录，避免夹带环境数据。
- 为核对基线，在私有临时目录用`git archive HEAD apps/api/src apps/api/tests`导出原始已提交源码（没有创建worktree、切换分支或复制真实卡库），以独立PYTHONPATH重放 `test_audit_batch02_admission.py::test_bound_target_back_cannot_be_ignored_by_front_only_marker_claims` 和 `test_image_service.py::test_card_vision_service_combines_front_and_back`：**2 failed**，两者在原HEAD上同样复现；后者断言旧提示词“正面和背面”而原HEAD已改为“候选正面和候选背面”。其余121项原因尚未逐项确定，不笼统声称全部为已知旧失败，也未越界改动其他识别/市场任务。
- 阶段检查由当前实现者自查，遵循用户不自动委派要求，没有独立代理复审；这不是整个028功能的最终发布审查。没有commit/push/PR，没有合并或生产发布。
- 本轮工作区的可用能力仅六个已启用只读工具；基础库已提供prepare/approve/execute，但真实导入/记账/研究handler尚未注册，不能把基础测试通过当作这些业务已经完成。后续从原清单继续，不能以此检查点替代全部目标。

## 继续实施：T015–T018代码闭环（2026-10-28）

- 新增captures.py、test_mcp_captures.py及0088中的私有intake/job映射表。URL只用于受限下载；持久化file_id、原图SHA256、私有相对引用、显式side/group，不保存短期URL。真实模型结果在多卡分析结束后持久化，取消/重启复用已分析结果，按确定键只补缺少的裁切子项；心跳保护活跃任务，耗尽恢复次数的遗留任务明确failed/RETRY_EXHAUSTED。
- prepare_card_import只创建草稿任务，单/双面复用CaptureService和scanner worker，多卡auto复用CardVisionService.recognize_sheet及实际裁切；保留采购草稿、原图和不确定项，不自动写收藏。多卡缺位置或超20张明确失败，不能把整图复制成多张确定实卡。
- prepare_capture_confirmation/execute_operation已共用父API的OperationService，handler调用CaptureService.confirm及原PortfolioRepository回读。冲突正背禁止确认；unknown配对在平台展示当前照片，并要求由登录用户勾选核图，批准API绑定摘要和acknowledgement；模型不能用confirmed字段代替。
- RED先观察缺captures模块；随后增补unknown配对未经核图即能approve、撤销后worker仍到模型、重启重复识别和耗尽任务卡在running等失败回归，再修复。当前12项集成测试通过，外部下载用真实受限下载器+MockTransport，模型仅在外部边界用明确合成结果替代；入库、事务、原图保存和并发均执行真实临时PostgreSQL/文件系统，不是假云端样本。
- 命令：`MCP_TEST_DATABASE_URL=<disposable> .venv/bin/python -m pytest apps/api/tests/test_mcp_*.py apps/api/tests/test_auth.py apps/api/tests/test_capture_media.py -q` → **102 passed，1条既有Starlette弃用警告，5.03秒**。`pnpm -C apps/web exec tsc --noEmit`退出0。后台intake随既有scanner worker开关启动并随API关闭清理；没有改生产worker/模型配置。
- T015/T016/T017/T018代码验收关闭；T003/T013/T014/T019真实附件、照片和跨端验收继续待办，未发布、不把合成样本当真实识别成功。

## T020–T024：收藏修改、记账、回收站（2026-10-28）

- RED：`t023-wiring-red.log` 捕获真实运行时缺少三个写工具；接入后54项定向回归通过。`sold-fee-red.log`复现清仓后费用被错误挂为剩余成本，修复共用账本计算后通过。
- `MCP_TEST_DATABASE_URL=<隔离库> .venv/bin/python -m pytest apps/api/tests/test_mcp_*.py apps/api/tests/test_auth.py apps/api/tests/test_capture_media.py apps/api/tests/test_portfolio.py apps/api/tests/test_identity_review.py -q`：150 passed。ruff通过；web `npx tsc --noEmit`通过。
- `test_mcp_workflow.py`：1 passed。通过真正Streamable HTTP `/mcp`、持久化hash token验证及平台approve HTTP路由，在独立测试owner依次执行买入、卖出、独立费用、备注校正、软删除、恢复；每步三次并发执行恰好一次生效，最终四条账本、零持仓、已实现185 CNY、原卡恢复且保留交易。此项为隔离真实数据库和协议链验收，不是ChatGPT/云端/手机验收；浏览器登录依赖注入为该测试owner，未伪称完整OAuth客户端授权实测。
- 身份校正调用抽取的原身份审核事务，产生版本/audit/估值任务；禁止私有MCP修改连带改变其他实卡共用产品赛季或共用定义标志。共享冲突返回平台审核恢复提示，是首版保守限制。
- 日期、币种、费用承担方、异币种汇率与依据、按日期超卖、变更后revision、他人资源、同键异参、三次重复/并发均有反例；sold卡只改备注不会重置held。永久删除未注册。
- 确认页改为中文身份和交易字段预览，隐藏内部hash、入账中间对象；真实浏览器视觉验收仍待发布。
- 测试只修改`manager_test_*`数据库及合成数据。未写真实用户持仓；本轮未部署。

## T032：账本区间报表（2026-10-28）

先运行不存在模块的5项RED；实现后发现显式1:1外币汇率的查询引用层级不符，新增定向反例捕获失败，再修复为原操作结果中的transaction_id。最终`test_mcp_reports.py test_mcp_workflow.py test_mcp_server.py`：13 passed。报表已注册MCP工具。

覆盖上海月初/月末半开区间、其他owner不可见、空账零值、回收站账本保留、清仓后独立费用、调整交易沿用原移动平均成本、混币原价分列、缺外币依据为null及显式1:1汇率。区间已实现盈亏按全部历史账本的期末减期初计算，不把期间现金流当利润。旧记录外币默认1且无明确依据时保守标缺汇率；无法从旧字段追认真实汇率。

真实云端固定月账和跨设备恢复仍属于T033/T040，未在本项冒充验证。

## T001设备版本补充（用户提供，2026-10-28）

iPhone ChatGPT App `1.2026.153`，Build `27158052172`。用户提供的实际版本已记录，尚未完成此版本插件/照片/撤销实测。Android设备仍未确认，不重复推断为有或无。

## T025–T027 / T030–T031：研究和后台任务代码（2026-10-28）

- 研究先运行4项模块不存在RED；实现后42项研究/原成交合同测试通过。新增平台审核HTTP权限/Origin/当前摘要/明确核图确认验证、保留旧估值历史与真实CLI validate共享核心回归。CLI prepare/validate提取为`domain/codex_sales_packet.py`共用核心：API容器不包含仓库scripts，避免运行不受控shell或复制另一套门槛；仓库CLI仍使用同一prepare/validate实现，实际子进程validate合成捕获结果39。
- `ResearchWorker`持久化队列，实际固定eBay已售检索与卡淘历史接口（沿用正式请求参数、Accept/X-Requested-With/Referer），保存每次请求参数/原文/状态/哈希及失败诊断。响应空壳、验证挑战、403、超时与真实空结果分开；本轮回归使用MockTransport，**未把合成响应或请求代码当真实市场搜索**。T028仍未完成。
- 不能自动核图的候选在平台研究页逐项补充，保存时服务器再取商品原文及公开实卡图；图片及汇率来源复用公共IP/DNS固定下载防护，原文只以text/plain呈现。用户查看当前捕获并批准审核后，满足原成交合同才可prepare发布；发布页另行批准。无候选/证据不足价格null；未知BestOffer/挂牌/lot/异版本/品相不符/本件历史/缺捕获/变更身份均不发布。审核仍需真实人核对，结构校验不证明描述为真。
- 研究启动/结果/发布、照片/研究批次、近期任务、失败项重试已接入运行时。批次整体验证后同事务排队；子项按原输入顺序持久化。RED发现UUID排序改变子项顺序，增加ordinal修复；另一RED复现单项缺照片中断整个批次，修复为逐项错误保留其他进度。
- 重试仅failed且识别为暂时来源故障，单任务最多3次；成功与待审核项不重跑，同一重试编号并发返回一次排队结果，异参冲突。研究结果与批次不随聊天关闭删除。真实断线/跨设备恢复仍为T033。
- 最终定向回归命令：`pytest test_mcp_*.py test_codex_sales_research.py test_portfolio.py test_identity_review.py test_auth.py test_capture_media.py`（完整路径同前文，隔离MCP_TEST_DATABASE_URL）：207 passed；ruff通过，web tsc通过。此计数不包括随后近期列表状态摘要的小调整，需再回归。
- 平台审核页仅通过TypeScript检查，真实浏览器视觉及ChatGPT调用尚未验收；T029未关闭。未部署/未写真实卡库。

## T034/T035/T038/T041 发布准备（2026-10-28）

- Skill经skill-creator初始化/quick_validate通过；私人包plugin.json/mcp.json按已下载Agent Plugins1.0.0公开schema校验通过。评估场景已编写，实际新聊天和三端安装仍待T036/T037/T040，未将本机包当云端分发。
- 发布配置和脚本定向RED→GREEN；增加MCP与well-known转发，固定内部api:8000 Host经421反例后允许，攻击者Host仍拒绝。与线上依赖版本一致的FastAPI0.143.2/Starlette1.8.0/Pydantic2.14.1私有兼容环境中，213项定向回归通过。
- 生产参数显式构建的Next15.5.20网页build通过，编译产物MCP目标验证为http://api:8000/mcp；新增连接撤销页面与实卡确认图预览。用户手册已写入docs/chatgpt-card-manager.md。
- 实际公网来源冒烟仅用隔离合成卡：北京时间02:47，eBay返回403，卡淘返回有效空列表；原文/SHA捕获保存在私有临时目录。不是两张真实卡验收，T028保持未完成。
- 云端现有镜像0.6.5-20261028-costattention1已确认；仓库目录源文件陈旧，实际installed runtime的config/main与本地仅差本功能，发布采用该镜像增量覆盖，保留成本提醒、视觉迁移、数据挂载及配置。未应用无关0087迁移。
- 第一次API构建被pip check阻止。已在原运行镜像复现相同rapidocr-onnxruntime1.4.4依赖opencv-python的元数据提示，实际cv2 4.14.0可导入。候选构建严格比较仅允许这条既有差异，并验证OpenCV导入与MCP1.30.0；不安装另一份OpenCV替换运行环境。此为已知基线限制，不宣称全依赖无冲突。
- 本段记录时新镜像仍在构建，尚未切换服务/迁移0088或创建ChatGPT授权。真实连接仍待验证。

## 云端增量发布（T039部分，2026-10-28）

API/web镜像已切换为0.6.5-20261028-chatgpt1，基于原costattention1增量构建。0088新增表迁移成功，未执行无关0087。原环境键（含省略键与镜像默认值）、挂载目标和Compose项目名称比较一致，保留原compose并通过额外override发布。候选镜像API导入/20工具注册、网页编译转发检查通过。

新API健康，web启动；容器运行验证10项全部true（成本worker开启、视觉deepseek-flash/显式thinking disabled、独立百炼搜索/qwen-plus、canonical OAuth/exact callback、SDK1.30.0）。公网验证4项全部true：MCP未授权401、protected resource发现、OAuth发现、确认页可访问。未写真实持仓或账本。

手机App覆盖安装、真实ChatGPT连接和三端业务仍未验收，T039暂不关闭。Chrome实际表单已重新发起元数据发现，尚未创建连接或授予访问。

## 真实私人连接与卡库读取（2026-10-28，续）

用户明确“允许，统一”后，私人自定义MCP插件“卡牌管家”已完成连接。发现并修复DCR未提供scope时默认注册只读、导致完整授权在进入平台前被SDK拒绝的问题；新增反例先失败，auth/server定向18项通过。默认注册允许申请四个支持范围，实际授权仍由用户同意决定。API增量镜像更新为0.6.5-20261028-chatgpt2，web仍为chatgpt1；仅修复新建且无授权的该条客户端注册元数据，未扩大既有用户授权。

通过本聊天实际连接的卡牌管家工具调用：get_profile返回“song的卡牌”及cards:read/cards:write/research:run/transactions:write；get_capabilities返回20个工具。search_cards(q=库里,page_size=3)总数34，前三项编号906、866、859；第三项为Dell Curry，故此宽泛检索不能声称34张均为Stephen Curry。get_portfolio_summary返回held_item_count903、已估值293、未估值610、CNY已知估值6666.620000、总估值null、基础币种成本23789.86000000000000、已实现0。总估值未知不能用采购成本补齐，也不能与网页历史成本兜底展示混作相同口径。

get_card_detail读取实卡#906（190b28fa-b157-47c7-b584-c0eed256828b），返回Stephen Curry、Hoops、Hoopnotic、HN-13及正背照片存在；历史估值明确标记ai_estimate与原日期，不算本次市场研究。get_card_image(front,preview)返回实际ImageContent，本聊天已显示原卡正面并可见NBA Hoops/Hoopnotic/Stephen Curry字样。此项证明当前连接的图片链可用，不证明iPhone/Android图片验收。无持仓、账本或估值修改。

当前云端自定义MCP插件版本1.0.0与本地含Skill便携包0.1.0是两种安装形态；尚未证明Skill已分发至真实ChatGPT客户端。T013/T014/T037/T039/T040继续保持未完成，直到各自完整验收要求满足。

## Skill真实账号安装（2026-10-28）

Chrome已登录账号进入“技能→添加技能→从电脑上传”，上传仅含SKILL.md、agents/openai.yaml、references/workflows.md的card-manager-skill-0.1.0.zip（无卡库/凭证）。UI显示“技能已上传”，关闭后“已安装”及“由我创建”均显示卡牌管家；详情skill_id=6ae159554b34819192cda367bd84676d，三文件可见，名称card-manager。通过“在聊天中试用”启动带Skill引用的新聊天，发送只读库里签字卡/前三张/首张正面/是否新研究场景，模型结果待核验。当前形态为同账号私人MCP连接+独立安装Skill，未声称已变成含两者的一体分发包或三端可用。

补充真实连接过滤探针：player=[Stephen Curry]总数34、autograph=true为61、numbered=true为203、graded=true为19、cost_min=100/cost_max=500为61。均仅只读，尚未做完整网页同账户12条逐项对照。插件权限查询工具按中文名及精确plugin ID均返回not_installed，但Chrome显示已安装且实际工具调用成功；该管理工具的解析/账户范围差异待核对，不能据此覆盖真实调用结果或推定权限配置。

设备重新枚举：iOS26.6.1 iPhone当前tunnelState=unavailable，另外两台disconnected；未覆盖安装或验证手机启动。网页端真实新聊天已返回profile/capabilities，但把列出的20个工具误计为19，保留为模型输出评估问题。

## 真实Skill聊天评估与两卡研究（2026-10-28 10:12，上海）

真实新聊天 https://chatgpt.com/c/6ae15968-ba18-83e9-8fd8-926f416effdd，可见模型GPT-6.1 Sol轻度。首轮试用保留系统自动填入的“编写示例”提示，模型改写示例漏掉库里条件，仅查全部61张签字卡，记为失败。第二轮明确同时保留球员和签字、区分Stephen/Dell后，模型正确返回Stephen Curry签字0张、Dell Curry #859 3/8一张；与本聊天直接q=库里/autograph=true结果一致，没有捏造不存在的Stephen签字图片。首轮模型声称#870图片“已打开并展示”，但网页截图仅见文字，未独立证实终端可见照片，不能以该句话关闭图片显示验收。当前聊天实际MCP ImageContent可显示两张卡正背。T036仅部分执行，其他场景待办。

12条真实查询请求/前三项稳定ID及总数记录于live-query-probes.json；暂未完成网页逐项对照/MCP Inspector。带连字符zzzz-no-such-player被现有发现搜索拆词并OR召回，返回66，不应当成严格空匹配断言；补不分词zzzznosuchplayerxyz返回0。沿用原搜索语义，不为测试改共享搜索。

真实两卡已看正背：#906 Stephen Curry 2025–26 Topps Hoops Hoopnotic HN-13（背面Topps版权2026），#859 Dell Curry 2023–24 Panini National Treasures Penmanship P-DCY红色3/8（背面赛季/产品/卡号可读）。具体平行名称尚未核实，不用猜测填Base；因此target_identity_unresolved保留。MCP start_card_research分别创建ec94b1d8-d58d-4f17-b6d5-194a48bf9716、b2c7685f-99f0-438b-a219-2fb550dc5554。两任务均真实尝试eBay和卡淘并保存原文SHA256；eBay HTTP403，卡淘有效空结果，无候选。价格均null、review_required，未审核或发布，不能关闭T028/T029。

实测暴露状态合同不一致：worker输出empty，既有研究校验只接受no_results，错误报cardhobby_history_search_not_executed。新增test_valid_empty_searches_satisfy_search_attempt_contract真实临时库回归先失败（empty != no_results），仅将两来源有效空结果映射修为no_results；空壳/挑战响应仍不算有效空。研究及原成交合同48项通过；完整相关215项通过（18.58秒，1条既有Starlette弃用警告），ruff/diff check通过。候选API chatgpt3基于chatgpt2仅覆盖research.py，导入与分类断言通过，发布后继续验证。原失败研究记录保留，不追认成成功或改写捕获。

### 修复线上验证及月报逐笔对照

API已切到0.6.5-20261028-chatgpt3并healthy；容器10项不变量与公网4项检查全通过，成本worker开启、DeepSeek显式禁思考与独立百炼搜索均保留。仅覆盖research.py，无数据库迁移。恢复发布须叠加release下search-outcome-fix/compose.fix.yaml，或同步主release override至chatgpt3，不能被旧chatgpt2覆盖回去。

新逻辑研究编号再次检索：#906 run=1fd6d195-c428-4428-8e75-b6767d1f7329；#859 run=1bb1a7f0-ce25-4be2-a950-d58b86ab4e28。eBay仍403、卡淘有效空结果现为no_results；assessment不再误报未搜索，仅保留target_identity_unresolved；价格均null、review_required。执行时间/来源/捕获SHA及引用见live-research-probes.json，原两次记录未改写。没有估值发布或卡库/账本变更；充分证据样本与候选审核仍待完成，T028/T029不关闭。

实际get_activity_report按Asia/Shanghai的2026-10-01T00:00至2026-11-01T00:00返回39笔CNY交易：买入1612.02、卖出0、费用65.71、已实现0，无缺汇率或异常账本。另通过SSH在生产PostgreSQL BEGIN READ ONLY中独立查询同owner/同半开区间，不过滤回收站：39个交易ID集合一致；逐笔display_id、交易类型、币种、数量、单价、费用全部一致，独立Decimal加总买入和费用与报表一致。原始逐笔材料保存在git忽略私有目录，不打包进Skill。此为T033月报部分，三卡部分失败/跨设备恢复仍未完成。

同一真实Skill聊天第三轮只读恢复任务/月报：模型正确找到4个研究记录、选择各卡最新任务，逐来源解释403与no_results、价格null与不可发布，区分历史AI估值，未发起新研究/重试/发布。月报买入1612.02、费用65.71、已实现0与直接工具和独立SQL一致，明确10月未结束、支出不能当亏损。该结果覆盖后台列表读取、研究不足表达及月报展示的部分真实模型场景，不替代三项批次失败恢复或充分证据发布验收。主release compose.chatgpt.yaml已备份并同步API为chatgpt3，后续标准重启不会回落到chatgpt2。
