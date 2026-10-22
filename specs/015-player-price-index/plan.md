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
### T030 球员行情与双端表达
- Files：domain/repository/api、card-core/player-prices.ts、web/mobile价格页面及对应测试。
- 从可比同款派生可解释的球员变化/活跃程度/置信与覆盖，固定比较构成；兼容旧DTO，三卡7日指数独立。真实数据快照验证排序与展开，避免只有空指数的列表。
### T031 发布、核验与持续更新
- 以当前线上镜像为基线，不用旧源码覆盖其他已发布功能。备份、构建、必要迁移、双端发布，核验worker/视觉/独立搜索、鉴权、真实路径与iPhone版本启动。运行两轮独立后台更新并观察历史游标推进。
### T032 完成审计
- 按spec v3逐项核对真实证据及原任务未完成项，更新validation-v3与发布记录；完成门槛不满足则继续补采/修复，不收缩目标。

Review focus：旧schema的缺失与实际篡改；卖家尾串与合法字母卡号；产品名相近但不是同款；源分页期间新成交导致移位；旧污染行不能因upsert永久excluded而无法恢复；已完成日更后历史饥饿；双端/线上基线差异。每项在所属任务验证。
