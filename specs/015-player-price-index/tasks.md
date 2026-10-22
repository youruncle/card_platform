# Tasks: 球员每日卡价

## Setup & Foundation
- [x] T001 在specs/015-player-price-index/维护唯一spec、plan、contracts及验收。
- [x] T002 在db/migrations/0081_player_price_index.sql建立独立样本、篮子、快照、状态表。

## US1 涨跌榜（P1）
- [x] T003 [US1] apps/api/tests/test_player_prices.py先验证三卡等权、低样本、时区边界、去重冲突，然后在player_prices/domain.py实现select_basket、calculate_snapshot。
- [x] T004 [US1] player_prices/repository.py与api.py实现账户过滤读取；测试空卡库、其他账户隔离、错误日期。
- [x] T005 [US1] apps/web/tests/player-prices.test.cjs先验证真实/演示分离、排序及明细，再实现player-price-board.tsx、player-prices.ts及app/player-prices/page.tsx、样式和主页入口。

## US2 代表卡发现（P1）
- [x] T006 [US2] apps/api/tests/test_player_prices.py先验证已售事实、精确同款/评级、未知版本拒绝，在player_prices/discovery.py实现保守解析+已有身份验证、别名分预算采集。
- [x] T007 [US2] player_prices/repository.py复用公开候选，独立样本去重，持久月篮子；PostgreSQL事务回滚测试幂等/月固定。

## US3 每日更新（P2）
- [x] T008 [US3] player_prices/service.py实现每日07:00、失败退避、刷新队列、锁及快照保存；main.py挂接，config.py后台开关，scripts/player_price_index.py手动工具。
- [x] T009 [US3] apps/api/tests/test_player_prices_postgres.py覆盖重跑、隔离、失败保留及后台到期逻辑。

## Polish
- [x] T010 运行聚焦回归、TypeScript检查、浏览器路径验证、真实公开样本回放；在specs/015-player-price-index/validation.md报告范围和限制。

Dependencies: T001→T002/T003/T006；T003+T006→T007→T008→T004/T009；T004→T005→T010。
Parallel opportunities: 纯计算、保守身份解析和前端独立文件可并行；本次原生顺序执行。

## 云端与iPhone测试版发布（用户已授权）
- [x] T011 共享价格类型/排序/名称；Mobile「我的」增加价格榜和来源明细，先验证排序、低样本、错误及队列，再实现。
- [x] T012 运行Web/Mobile构建和发布相关测试，准备无私有数据的可复现发布包；更新版本。
- [x] T013 备份云端数据库/程序/镜像/编排，构建后顺序迁移与切换，验证价格鉴权、账户隔离、原有接口及静态资源。
- [x] T014（签名、安装、版本与启动已验证；域名公网HTTPS及用户真机登录于2026-10-16通过，其他未验证范围已记录）同步并编译签名iOS工程，安装自己的已配对开发iPhone；验证版本与启动，记录HTTPS连通性和真机尚未验证范围。
- [ ] T015（发布与回退记录已完成；本次浏览器截图读取超时，未补截图）更新发布记录、回退命令和截图，报告实际发布/安装结果。

- [x] T016（2026-10-16）安装下载的cardsp.cn证书到Nginx；备份配置、私钥保护、来源校验与Secure Cookie、公网TLS/静态资源/登录接口及临时账户登录回归通过，记录于https-deployment.md。


## v2 行情可用性优化（2026-10-17）
- [x] T017 独立90日行情/30日趋势、可比卡优先和达标后固定；纯函数回归。
- [x] T018 独立商品诊断/拒绝原因与未固定精确补采；采集回归。
- [x] T019 旧快照兼容行情、概览、单球员刷新；API/数据库隔离回归。
- [x] T020 Web/Mobile行情、原因、概览及单球员刷新；交互回归。
- [x] T021 只读审计、真实样本/测试/构建、审查与验证记录。


## v2 双端发布（2026-10-18，用户已授权）
- [x] T022 线上基线核对、完整数据库和源码/镜像备份；冻结价格范围发布包。
- [x] T023 构建并发布API/Web，预览、真实只读范围校验与公网检查。
- [x] T024 iOS1.1.7/build11编译、签名、覆盖安装、设备版本读回与启动。
- [x] T025 配置独立SSH部署公钥并验证全新连接免扫码，保留扫码策略；记录双端发布约定与回退。

## v3 真实行情可用（2026-10-22，持续推进）
- [x] T026 公开观察旧结构兼容，真实事实与原始hash校验保持；失败→通过回归，云端独立只读回放。
- [ ] T027 卖家串码/正式卡号隔离及卡款身份纠错，云端旧派生数据shadow审计与可回退修复。
- [x] T028 持久历史游标、增量补采、限速预算与公平后台调度，跨轮/失败/幂等回归；真实公开源两轮推进已验证，线上部署后持久化/持续运行验收仍属T031。
- [ ] T029 固定真实验收组、针对性卡款核验和补采，至少30笔纳入核验及三个报告日真实回放。
- [ ] T030 可解释球员行情、活跃程度与证据覆盖，两端有效排序/明细/缺失状态。
- [ ] T031 网页/API/iPhone发布、真实鉴权路径及两轮独立后台更新，保留生产不变量和回退。
- [ ] T032 按spec v3全部完成门槛审计；补齐尚未满足项后再标记任务完成。
- [ ] T033 修复当前线上联网搜索与视觉混用DeepSeek端点/密钥的配置及代码；恢复已备份的独立百炼qwen-plus搜索链路，缺配置不发送请求，验证实际请求/来源捕获与生产不变量后再发布。

本轮基线：82ef5f7d；价格/公开拍卖聚焦基线65通过、1失败（既有视觉prompt文案断言），不将该失败计为本轮新引入。业务基线及证据路径见spec v3。
