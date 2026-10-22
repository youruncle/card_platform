# 球员每日卡价 Implementation Plan

**Goal**: 交付可预览、可追溯、按日更新的球员三卡涨跌榜。
**Architecture**: 独立player_prices模块，复用卡淘已售解析/身份校验和公开样本。新增独立采样、篮子、快照、状态表；不改原估值数据。后台每分钟检查到期球员，单球员事务锁保证幂等。
**Tech Stack**: Python 3.12、FastAPI、psycopg、PostgreSQL、Next.js 15、React 19、TypeScript；无新依赖。
**Spec**: [spec.md](spec.md)
**Execution**: 当前会话原生实现；用户已明确授权“先做一版”，不重复审批设计。不切分支、不复制未提交工作、不提交无关文件。

## Technical Context
默认覆盖自己活动篮球卡库球员。上海07:00处理上一日，刷新请求由持久状态排队。采集最多12页（60条/页），每次请求间隔至少1秒，最多2个球员别名和3个代表卡精确词；部分失败记录覆盖及状态，保留旧快照。

## Constitution Check
通过：沿用现有架构；新增表独立存储；证据来源可追溯；未知字段拒绝；用户读写范围绑定owner；不修改估值/成本。设计后复核通过。

## Project Structure
- apps/api/src/card_platform/player_prices/: domain.py纯计算，discovery.py身份分组/采集，repository.py存取，service.py工作调度，api.py鉴权接口。
- db/migrations/0081_player_price_index.sql: 独立表与索引。
- apps/web/app/player-prices/: 页面及独立样式；components/player-price-board.tsx、lib/player-prices.ts。
- apps/api/tests/test_player_prices*.py、apps/web/tests/player-prices.test.cjs。
- main.py最小路由/后台挂接；config.py新增后台开关；app/page.tsx新增入口。

## Global Constraints
每组每窗最小3笔；固定三卡等权；只用CNY成交价不含运费；自然日窗口参照spec FR-001；近90天选择；月度固定；无跨账号暴露。

## Review Focus
重复商品冲突必须整体剔除；未知版本不能自动当base；当天未结束不进入快照；月初两个窗口必须同篮子；部分采集失败不得冒充新有效结果。

## Tasks
执行步骤与验收统一维护于[tasks.md](tasks.md)，每个交付先写失败测试、观察失败，再实现并确认通过。无需额外Superpowers计划文件。

## 发布补充

将价格DTO、排序、同款名称抽取到 packages/card-core/src/player-prices.ts；Web保留原有导出，Mobile在「我的」展示原生价格页，通过MobileApi沿用Bearer、取消及过期处理。发布包只含程序、共享源和迁移。先保留旧程序包/镜像/编排和数据库备份，在服务保持运行时完成构建；切换前退出旧API，应用增量迁移并启用新API，然后切换Web。保留原服务器工作进程开关、数据库与持久卷；只显式启用新价格工作进程。iOS使用已存在的匹配签名身份与设备开发描述文件，先构建，再安装到配对且开发模式已启用的iPhone；无需TestFlight。


## 2026-10-17 v2 implementation plan
用户已确认方案并要求实施，当前会话原生执行，沿用当前功能分支，不自动提交/发布。
1. domain.py新增calculate_market(rows, report_date)：独立成交90日行情、最多20卡款、30日非重叠窗口；select_basket优先近期可比，compute仅ready时固定。先测试单卡、边界、冲突、截断和旧指数。
2. discovery.py新增独立观察去重及拒绝原因；service统计诊断，已有确认成交生成未固定阶段精确查询。先测试重复/冲突与精确补采，保留失败旧指数。
3. repository.board批量读取账户范围内90日成交派生market和summary；enqueue支持可选player_id；API校验UUID/账户范围。先测试旧快照、历史日、账户隔离及404。
4. card-core新增可选market/summary兼容类型与状态函数；Web/Mobile独立行情、概览和单球员刷新；先写单卡/异常/低样本/刷新交互测试。
5. scripts/player_price_audit.py只读审计；真实样本、后端/两端测试及类型/构建、审查并记录验证。
Review focus：未来成交、冲突重复、旧接口/快照、账户隔离、采集失败与已有行情共存。既有篮子和指数不追溯改写。

## 2026-10-22 v3 execution plan

**Goal**：修复证据/身份/历史采集链路，以真实可比成交交付可用球员行情。
**Execution**：沿用当前分支原生顺序执行；本轮已获持续推进及双端发布授权，不为已授权步骤重复审批。使用brainstorming、writing-plans、executing-plans与TDD，进度统一记录本目录tasks及validation-v3，不生成第二份计划。当前基线82ef5f7d，工作区开始时干净。
**选择**：不降低成交与身份门槛；先恢复历史证据和移除错身份，再做可恢复补采，最后根据真实覆盖决定球员行情呈现。仅放宽门槛会产生伪趋势；仅修改页面仍无可比历史。

### T026 证据兼容
- Files：public_source_catalog.py、tests/test_public_source_compatibility.py。
- 先复现缺少seller_id/consignment_code为null的旧捕获被误拒，再只兼容新增可选字段的无信息默认；验证价格/日期/标题/运费/评级/hash/未知字段篡改仍拒绝。运行新测试及公开成交/行情聚焦回归。
### T027 身份与派生数据
- Files：player_prices/discovery.py、tests/test_player_prices.py、player_prices/repository.py；必要时新增同模块repair.py与专用只读/应用CLI。
- 先用实际XBW10尾串标题复现，再隔离卖家编号与真正卡号，补正例/反例。云端shadow回放全部旧成交，输出改变/退出/可修复记录及原因；保存审计后事务化修复派生表，可回退，不把程序升级误当成源事实冲突。
  另处理同一公开球员不同UUID互相排除成交：已有文班和东契奇重复主体实证。统一公开价格主体/卡款键、归集公开成交与刷新调度，保留原私有持仓及账户授权边界；先复现两UUID相同成交被永久excluded，再验证单主体展示、幂等与隔离。
### T028 历史与增量采集
- Files：player_prices/discovery.py、repository.py、service.py、新增迁移/采集状态（如需要）、测试player_prices/player_market/postgres。
- 接口：collect_player保留现有位置参数，新增可选持久查询状态；coverage返回精确历史游标/边界/完成状态。失败不推进；最新增量和历史共享预算；后台在日更后仍可公平处理历史未完成对象。测试跨轮、失败重试、边界、变更查询及幂等。
### T029 真实卡款召回与云端回放
- 优先复用云端公开目录、正式卡款知识及原图；只在证据足够时补身份，保留字段来源。先确定验收名单，恢复历史证据后观察真实缺口，再针对性详情/图片核验及精确补采。报告至少3个完整报告日，不追溯写入未来成交。
  已接入price专用catalog：读取云端当前published snapshot，官方制品校验hash/路径/产品/赛季范围；普通Base与变化/平行section严格分开，命中特卡英文名时保留特卡。官方目录仅确认球员/产品/赛季/卡号/卡种，不填物理平行、限编、评级。清单卡号可生成discovery-only查询种子；种子不包含价格、不作为成交。刷新按当前来源标题重核目标，不直接沿用旧篮子/缓存。发布版本切换替换缓存，错误按本次刷新实际资料范围隔离，资料缺失不能冒充零成交成功。
  下一段实现/核验：公共商品图库须与原观察及primary image绑定，保留图库HTML与图片hash；视觉身份与配对分别核验，字段证据缺失保持拒绝，不能把官方清单的存在当物理版本证据。图片核验在有预算的独立处理阶段完成，不在用户读请求内批量发模型调用。先以冻结30笔实际成交做人工对照，再扩大补采和三个报告日回放；已验收身份收据可复用，知识发布变化需重新执行确定性官方核对。
  30笔冻结样本已由Codex逐笔目视对照（不是用户人工签核）：21笔图文一致、2笔错版本、4笔图片不足、3笔表面名称待按图归一。不能将30笔“已看过”等同30笔“可靠纳入”。所有拟纳入身份都需接受实图对照/可追溯收据门槛，不能只检查缺卡号的候选。复合颜色不得被单色后缀吸收；Base Prizms Ice等官方变化section不能因父名Base当成普通Base，需按精确官方section核对而非维护不完整颜色列表。
  实现顺序：image_evidence.py保存/验证独立公开图片观察收据（原始观察hash、图库HTML和两面图片blob hash、实际模型/非思考参数、逐字段图像位置、配对、裸卡/评级/限编/品相）；image_reader.py有界读取准确图库并使用专用精简结构提示，不向模型提供卖家标题作为图片事实；image_jobs.py与独立迁移保存候选/租约/退避/每日预算，网络和模型调用在数据库事务外。当前publication仅做确定性卡款核验，变化时重核收据，不重复无变化的模型观察。refresh仅纳入完整有效收据的成交，同时保留标题候选供补采和核验排队；历史窗口按当前/前期均衡选样、公开主体公平调度。旧派生价格通过独立可回退repair重建，不修改私有持仓/估值/成本。
  收据/有界读取模块的隔离回归已通过；当前两面契约仅接收恰好两张单卡图，多图另做完整复核。16次真实模型调用仍0准入，不能先接入放大调用；下一步先试独立逐面观察/服务器合成，必要时用经过实际版本核验的公开参考图辅助。完整保留不利样本、原模型输出及未知，不靠标题补造视觉事实或反复叠提示。持久队列须保存服务器生成的观察/收据与持久卷字节，不能接收客户端伪造的receipt；同一原观察hash重用，变更时失效。
### T030 球员行情与双端表达
- Files：domain/repository/api、card-core/player-prices.ts、web/mobile价格页面及对应测试。
- 从可比同款派生可解释的球员变化/活跃程度/置信与覆盖，固定比较构成；兼容旧DTO，三卡7日指数独立。真实数据快照验证排序与展开，避免只有空指数的列表。
### T031 发布、核验与持续更新
- 以当前线上镜像为基线，不用旧源码覆盖其他已发布功能。备份、构建、必要迁移、双端发布，核验worker/视觉/独立搜索、鉴权、真实路径与iPhone版本启动。运行两轮独立后台更新并观察历史游标推进。
### T032 完成审计
- 按spec v3逐项核对真实证据及原任务未完成项，更新validation-v3与发布记录；完成门槛不满足则继续补采/修复，不收缩目标。

Review focus：旧schema的缺失与实际篡改；卖家尾串与合法字母卡号；产品名相近但不是同款；源分页期间新成交导致移位；旧污染行不能因upsert永久excluded而无法恢复；已完成日更后历史饥饿；双端/线上基线差异。每项在所属任务验证。
