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
