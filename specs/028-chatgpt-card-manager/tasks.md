# Tasks: ChatGPT 卡牌管家

**Input:** [spec.md](spec.md)、[plan.md](plan.md)、[research.md](research.md)、[data-model.md](data-model.md)、[contracts/mcp-tools.md](contracts/mcp-tools.md)。

**Created:** 2026-10-28。**状态：实施中，开发/接入任务29/42完成。** 2026-10-28更新；以复选框和verification.md实证为准，接入/设备待验。

**执行规则：** 默认当前聊天逐项推进，不自动启动多个开发代理。每个行为任务先写有效失败用例、确认失败、最小实现、运行相关测试；验证通过并在本目录 `verification.md` 记录任务ID、命令/版本/结果及真实样本后才勾选。外部阻塞记录真实尝试、所缺输入和恢复动作，可继续不依赖它的任务；不能勾选阻塞任务或宣称整阶段完成。

## 阶段0：Setup — 接入预检（T001–T003）

**交付/验收：** 确定实际账号的自定义连接、私人Skill分发、网页/手机附件和私有图展示路径。官方文档核查不能代替账号实测。

- [ ] T001 在 `specs/028-chatgpt-card-manager/verification.md` 记录实际账号自定义MCP/写工具权限、私人插件和Skill云端分发入口，以及浏览器/iPhone/Android可测试版本；逐项标可用/受限/未测和恢复动作，不用本地安装代替跨端安装。（US6；FR-010）
- [x] T002 在 `specs/028-chatgpt-card-manager/research.md` 固定兼容Python>=3.12的官方MCP SDK、OAuth库/身份服务版本，确定现有账户绑定、PKCE S256、issuer/resource、redirect URI及refresh撤销方案；更新同目录 `contracts/mcp-tools.md` 的部署参数定义。（FR-006）
- [ ] T003 在私有临时目录做可丢弃MCP附件/图片探针，测试顶层files数组、单面/正背/三附件及授权图片回传；将三端真实结果、最小配置和受限项写入 `specs/028-chatgpt-card-manager/verification.md`，清理临时公开入口。探针不得读取或写入真实卡库。（US2/US6；FR-003/010）

## 阶段1：Foundational — 云端MCP、认证和可靠操作（T004–T010）

**交付/验收：** 可认证、可撤销、限scope、按账户隔离，待确认操作在重复/并发下只执行一次。T001/T003不通过时不宣称跨端可用；本地隔离实现仍可推进。

- [x] T004 在 `apps/api/tests/test_mcp_server.py` 先验证initialize/tools/list/错误schema及生命周期，再创建 `apps/api/src/card_platform/mcp/server.py`、`schemas.py`、`__init__.py` 并增量修改 `main.py`、`apps/api/pyproject.toml`；挂载唯一 `/mcp`，复用数据库及关闭清理，未交付工具不注册。
- [x] T005 在 `apps/api/tests/test_mcp_auth.py` 先验证PKCE失败、错误audience/issuer/resource、过期、scope缺失、刷新重放及撤销；实现 `apps/api/src/card_platform/mcp/auth.py`、`oauth_api.py` 和 `apps/web/app/chatgpt/authorize/page.tsx`，使用T002标准组件绑定现有登录，Cookie/mobile session行为保持兼容。
- [x] T006 在 `apps/api/tests/test_mcp_storage.py` 以临时PostgreSQL验证迁移及唯一约束，新增 `db/migrations/0088_chatgpt_manager.sql`（实施前重查编号）和 `apps/api/src/card_platform/mcp/schemas.py`；落实 `owner_id/connection_id: UUID`、scopes白名单、`idempotency_key: UUID`、唯一 `(owner_id, kind, idempotency_key)`，授权/操作/批次不另建收藏主库。
- [x] T007 在 `apps/api/tests/test_mcp_operations.py` 先覆盖同键同参/异参、摘要篡改、15分钟过期、跨账户及连接切换；实现 `apps/api/src/card_platform/mcp/operations.py` 的prepare/lookup和 `payload_hash`、`expected_revision` 快照，成功记录首版不自动清理。
- [x] T008 在 `apps/api/tests/test_mcp_operation_approval.py` 先覆盖未登录、CSRF、他人操作、变更摘要和失效连接；实现 `apps/api/src/card_platform/mcp/operation_api.py` 及 `apps/web/app/chatgpt/actions/[operationId]/page.tsx` 的具体影响预览/批准，`confirmed=true` 无效，approve不注册为MCP工具。
- [x] T009 在 `apps/api/tests/test_mcp_operations.py` 用测试handler验证并发execute仅一次、业务失败回滚、确认后revision冲突及成功回读；完成 `apps/api/src/card_platform/mcp/operations.py` 的执行锁、handler协议、原子结果保存及原scope重查，身份/收藏/账本快照均参与revision。
- [x] T010 在 `apps/api/tests/test_mcp_capabilities.py` 先验证只返回启用工具、profile及scope、错误不泄密，再完善 `apps/api/src/card_platform/mcp/server.py`、`auth.py`、`schemas.py` 的 `get_capabilities/get_profile`、工具annotations、审计及日志脱敏；每次调用由principal决定owner。

## 阶段2：US1 — 查询卡库、图片和组合（T011–T014，P1，最小可用版）

**独立验收：** 12条固定查询/详情/汇总与云端同账户结果一致，其他用户资源不可见；只读MVP不代表全部目标完成。

- [x] T011 [US1] 在 `apps/api/tests/test_mcp_portfolio.py` 写12条请求及越权反例，覆盖中文别名、签字/限编/评级/成本/持仓、同款多实卡、分页排序、空组合、未估值及异币种；明确预期item IDs与账本口径。
- [x] T012 [US1] 实现 `apps/api/src/card_platform/mcp/portfolio.py` 的 `search_cards/get_card_detail/get_portfolio_summary`，复用 `SearchFilters`、排序和 `PortfolioRepository`；`page_size` 为1–100、默认30，未知过滤字段拒绝，结果含稳定编号与revision，不用搜索别名证明卡片身份。
- [ ] T013 [US1] 在 `apps/api/tests/test_mcp_media.py` 验证他人资源/撤销/错误side，再实现 `apps/api/src/card_platform/mcp/media.py` 的 `get_card_image`；沿用私有媒体所有权和revision，以T003实测方式返回图，不泄露任意路径或假设Cookie图跨端可读。
- [ ] T014 [US1] 通过MCP Inspector和实际ChatGPT连接重放12条固定请求，将脱敏请求、真实结果及网页同账户对照写入 `specs/028-chatgpt-card-manager/verification.md`；缺实际连接时仅记录代码测试，不能完成本项。

## 阶段3：US2 — 拍照识别、校正和入库（T015–T019，P1）

**独立验收：** 单面/正背/三卡/多卡图各一组真实样本，确认后平台可回读；每组三次重试只产生一次入库结果。

- [x] T015 [US2] 在 `apps/api/tests/test_mcp_capture_files.py` 先覆盖缺files/side映射、附件过期、同键异图、私网/重定向/DNS变更、流式超限及解码错误；文件字段严格声明download_url/file_id/mime_type/file_name且仅前两项required，`files` 数量1–20，单文件最大 `10 * 1024 * 1024` 字节。
- [x] T016 [US2] 完成 `apps/api/src/card_platform/mcp/media.py` 的受限HTTPS附件下载、逐跳地址验证、超时/字节及既有像素预算、JPEG/PNG/WebP嗅探和云端私有保存；短期download_url不落业务库或日志，不能把同图去重当同款实卡合并。
- [x] T017 [US2] 在 `apps/api/tests/test_mcp_captures.py` 先测单/双/多图及错配，再实现 `apps/api/src/card_platform/mcp/captures.py` 的 `prepare_card_import/get_job_status/prepare_capture_confirmation`；files顶层fileParams声明、sides等长明确配对；复用CaptureService和现有多卡识别，保留原图/证据/采购字段及人工校正。
- [x] T018 [US2] 在 `apps/api/tests/test_mcp_captures.py` 覆盖批准/执行、三次重试、revision变化及缺照片，向 `apps/api/src/card_platform/mcp/operations.py` 注册import handler并调用 `CaptureService.confirm`；owner仅取principal，返回item_id及云端回读。
- [ ] T019 [US2] 用真实单面、正背、三卡和多卡图完成识别/校正/确认，补一个正背不匹配反例，在 `specs/028-chatgpt-card-manager/verification.md` 记录原样本私有引用、任务/卡片ID、三次重试及网页/现有App回读；不确定字段保持未知。

## 阶段4：US3 — 收藏校正与买卖记账（T020–T024，P2）

**独立验收：** 六类变更均可预览、确认、回读；并发/重试不多账，不误改身份或持仓。

- [x] T020 [US3] 在 `apps/api/tests/test_mcp_mutations.py` 先覆盖相似实卡误选、sold卡部分patch不变held、身份修订走审核、币种/日期/费用承担不明、超卖、重复和并发冲突；交易固定 `quantity > 0`、`unit_price >= 0`、`fee_amount >= 0`，金额使用Decimal。
- [x] T021 [US3] 在 `apps/api/src/card_platform/mcp/mutations.py` 实现 `prepare_card_change` 和update handler；只处理实际提供字段，身份改动适配现有身份审核服务，局部抽取共享逻辑时仅改相关文件；确认快照发生变化返回REVISION_CONFLICT。
- [x] T022 [US3] 在 `apps/api/src/card_platform/mcp/mutations.py` 实现 `prepare_transaction` 和transaction handler，复用 `PortfolioRepository.add_transaction` 及同事务幂等；三字母币种、有时区日期、费用方向明确，缺外币折算依据不得默用1，返回交易ID和持仓/账本回读。
- [x] T023 [US3] 在 `apps/api/tests/test_mcp_trash.py` 先测越权/重复/有历史交易，再实现 `apps/api/src/card_platform/mcp/mutations.py` 的 `prepare_trash_change` 及trash/restore handler，复用soft_delete_item/restore_item，工具列表不含永久删除。
- [x] T024 [US3] 对隔离测试卡实际执行买入、卖出、费用、校正、软删除和恢复，确认重复/并发与收益回读；将逐步结果写入 `specs/028-chatgpt-card-manager/verification.md`，不得为验收更改无关真实持仓。

## 阶段5：US4 — 真实成交研究与估值（T025–T029，P2）

**独立验收：** 两张真实卡分别覆盖充分证据与不足/受限；两个市场均有本次实际尝试捕获，审核、价格和发布可追溯。

- [x] T025 [US4] 在 `apps/api/tests/test_mcp_research.py` 先覆盖旧结果冒充新搜索、单市场未尝试、403/超时、未知BestOffer、挂牌/lot、版本/评级/品相不符、重复实卡和未审核候选；不满足现有研究合同返回null或review_required，结构validate成功不能代替真实性审核。
- [x] T026 [US4] 实现 `apps/api/src/card_platform/mcp/research.py` 的 `start_card_research/get_research_result` 与云端后台适配；受控调用 `scripts/codex_sales_research.py` prepare/validate及实际双市场检索/捕获，绑定卡面SHA256和身份版本，原币种/运费/日期/真实金额分列；无法自动核图时进入人工审核，不伪装完成。
- [x] T027 [US4] 在 `apps/api/tests/test_mcp_research_publication.py` 先测缺捕获、审核未完成、身份变化和任填price，再实现 `apps/api/src/card_platform/mcp/research.py` 的 `prepare_research_publication` 及publish_research handler；发布只能引用验证审核后的研究结果，保留旧估值历史。
- [ ] T028 [US4] 选择授权范围内两张真实卡，实际搜索eBay及卡淘、留原文和实卡图捕获，逐候选核图/版本/品相/日期/真实金额/币种/运费/重复审核并运行prepare/validate；将真实尝试、阻塞恢复及审核引用写入 `specs/028-chatgpt-card-manager/verification.md`，新搜索未做不得勾选。
- [ ] T029 [US4] 在 `apps/api/src/card_platform/mcp/research.py`、`schemas.py` 完成研究日期/合同版本/各来源缺口/审核状态输出；通过实际ChatGPT展示充分与不足样本并回读批准发布结果，在 `specs/028-chatgpt-card-manager/verification.md` 确认不足价格为null，无硬编码经验价或成本补价。

## 阶段6：US5 — 批次、后台进度与月度报表（T030–T033，P3）

**独立验收：** 三项批次断线后恢复，成功项不重跑；固定月份报表逐笔一致，缺汇率不混加。

- [x] T030 [US5] 在 `apps/api/tests/test_mcp_jobs.py` 先覆盖他人批次、重复创建、部分失败、终态映射与重试上限；批次 `item_refs` 数量1–20，`retry_count` 首版最多3次，仅failed且retryable可重试，同owner同键异参409。
- [x] T031 [US5] 实现 `apps/api/src/card_platform/mcp/jobs.py` 的 `create_batch/list_jobs/get_job_status/retry_job`，绑定既有capture/research任务，持久化逐项引用和cursor；limit默认20、范围1–100，聊天结束不停止任务，重试不重跑succeeded。
- [x] T032 [US5] 在 `apps/api/tests/test_mcp_reports.py` 先测月末/跨时区、零持仓、异币种/缺汇率和退款调整，再实现 `apps/api/src/card_platform/mcp/reports.py` 的 `get_activity_report`；区间 `start <= transacted_at < end`、默认 `timezone=Asia/Shanghai`，start必须早于end，按账本确定性聚合并分列买卖/费用/已实现盈亏。
- [ ] T033 [US5] 实际提交三卡批次并模拟一项失败，断开聊天后在另一设备/新聊天恢复并仅重试失败项；用固定月账核对报表，在 `specs/028-chatgpt-card-manager/verification.md` 记录批次ID和逐笔对照。

## 阶段7：US6 — Skill与私人插件（T034–T037，P1）

**独立验收：** 同一私人插件及Skill在网页/手机可调用，流程只使用已交付工具；此阶段不替代末期真实业务全流程验收。

- [x] T034 [P] [US6] 创建 `plugins/card-manager/skills/card-manager/SKILL.md`、`references/workflows.md`、`agents/openai.yaml`；写清查询消歧、照片草稿确认、交易日期/币种/费用、幂等回读、双市场证据/null、批次恢复及能力缺失处理；依赖远程工具，不打包密钥、真实卡库快照或只在本机运行的脚本。
- [x] T035 [P] [US6] 创建 `plugins/card-manager/plugin.json`、`mcp.json`、`README.md`，按官方当前schema固定版本、远程依赖及私人安装路径；manifest校验，不建立本机绝对路径依赖，不把本地marketplace冒充云端分发。
- [ ] T036 [US6] 创建 `plugins/card-manager/evals/scenarios.md`，覆盖6故事、直接/间接/后续请求、缺信息、越权、确认取消、证据不足、负例与未交付能力；实际新聊天运行评估，逐项写入 `specs/028-chatgpt-card-manager/verification.md`，修复触发及工具序列问题。
- [ ] T037 [US6] 按账号支持的私人/工作区渠道连接、安装含Skill插件，分别在浏览器/iPhone/Android新聊天调用profile、查询、近期任务及附件探针；在 `specs/028-chatgpt-card-manager/verification.md` 记录可复现安装路径、版本、权限及限制，不要求公开上架。

## 阶段8：Polish与发布验收（T038–T042）

**交付/验收：** 云端运行、权限撤销、三类设备主要操作、现有网页/App一致性和生产默认配置全部有实证。

- [x] T038 在 `apps/api/tests/test_mcp_deployment_config.py` 验证新配置默认值与生产不变量，增量更新 `apps/api/src/card_platform/config.py`、`infra/compose.prod.yaml`、相关反向代理及 `.env.production.example`，创建 `scripts/verify-chatgpt-manager.py`；仅记录配置名/断言，检查worker开启、视觉deepseek-flash显式thinking disabled、独立百炼搜索，不输出凭证。
- [ ] T039 按本功能范围发布云端MCP/OAuth、API、网页及当前个人iPhone签名渠道的App，验证健康、OAuth发现/回调、原登录、MCP工具、容器实际 `PURCHASE_COST_WORKER_ENABLED=true` 及模型/搜索隔离；在 `specs/028-chatgpt-card-manager/verification.md` 记录版本、启动及恢复命令，保留现有数据/远端配置。
- [ ] T040 在电脑浏览器、实际iPhone、实际Android分别完成查询、拍照入库、记账、批次恢复和授权撤销，检查网页/现有App回读与用户隔离；将设备/系统/ChatGPT版本和结果写入 `specs/028-chatgpt-card-manager/verification.md`，缺设备或账号限制保留未完成。
- [x] T041 创建 `docs/chatgpt-card-manager.md`，提供私人安装/连接、常用中文指令、图片与交易确认、后台进度、估值证据、撤销、重试、版本升级和常见失败恢复；链接本目录 `tasks.md`，明确开发任务清单和用户业务任务状态的区别。
- [ ] T042 按 `specs/028-chatgpt-card-manager/quickstart.md` 运行相关测试与实际端到端验收，核对FR-001–015及SC-001–007；更新本目录 `verification.md` 与 `tasks.md` 的证据和实际计数，外部缺口/未测项非零时不得宣称完整目标完成。

## Dependencies & Execution Order

- 主线：T001→T002→T003；T004→T005→T006→T007→T008→T009→T010；随后按各故事顺序推进。
- T001/T003若因账号/设备阻塞，T002及本地隔离基础可先做；所有真实连接/手机验收仍保持待办。T006实施须在T005确定身份组件实际存储需求之后。
- US1依赖基础；US2依赖基础及媒体T013；US3依赖基础并使用US1消歧/回读；US4依赖基础及US1身份/图片，但不依赖US3记账；US5依赖capture/research任务适配。
- T034/T035在工具合同稳定且T029/T033完成后可分别编写技能和manifest（文件不重叠），默认顺序执行。T036依赖两者，T037依赖实际私人分发可用。
- T038之后才能生产发布；T039之后执行T040。T042依赖全部必要交付及真实验收；某故事代码测试通过不能勾选该故事真实样本任务。

## 故事数量、并行机会与增量交付

US1：4项；US2：5项；US3：5项；US4：5项；US5：4项；US6：4项；Setup/Foundation：10项；最终发布：5项；总计42项。

可独立工作示例：基础完成后US1的查询测试与媒体测试可在隔离文件准备；US2稳定后US3和US4的独立测试材料可分别准备；US5的jobs/reports测试互不依赖；US6的T034/T035文件互不重叠。以上仅说明依赖与文件边界，不授权自动启动代理。

最小可用版为阶段0–2：真实账号在ChatGPT查询云端卡库。之后依次开放入库、管理、研究、批次及完整技能，最终完成三端验收；全部阶段仍属于本次完整目标。

## 需求覆盖

| 需求 | 主要任务 |
| --- | --- |
| FR-001、FR-002 | T011–T014、T039–T040 |
| FR-003 | T003、T015–T019 |
| FR-004、FR-005 | T007–T009、T018、T020–T024、T027 |
| FR-006 | T002、T005–T010、T013、T040 |
| FR-007 | T025–T029 |
| FR-008、FR-009 | T030–T033 |
| FR-010 | T001–T003、T034–T037、T040 |
| FR-011、FR-012、FR-013 | T001、T006、T034、T041–T042及全局约束 |
| FR-014 | T038–T039 |
| FR-015 | 各故事测试/真实回归、T040、T042 |

SC-001→T014；SC-002→T019；SC-003→T024；SC-004→T028/T029；SC-005→T033；SC-006→T040；SC-007→每项closure记录和T042。
