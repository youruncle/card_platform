# 024 任务

- [x] T01 冻结独立样本、原始证据和代码哈希，运行基线。
- [x] T02 复现商品多图与字段类型问题；先写失败测试，实现受限图组和结构化观察。
- [x] T03 集成当前图片/身份绑定与复核，缓存隔离及失败阻断。
- [x] T04 验证并修复多人查询、Insert 简称、位置/贴纸卡号。
- [x] T05 复现单元缺失与状态过期，验证幂等和财务/分配保护，补追踪。
- [x] T06 同一独立集/开发集真实回放与反例，逐卡前后比较。
- [x] T07 运行针对性及 API 测试，审核实现，报告未完成/未验证项与部署状态。

所有项的实际范围和未覆盖项见 validation.md。T06 的完成表示已执行并报告独立验证，不表示成本恢复率提升；评估阶段未部署或写真实数据；后续部署见 deployment.md。

- [x] T08 按用户授权发布网页、云端 API 和个人签名 iPhone 版本；验证公网、备份、版本与启动，记录回滚及未完成检查。

## 100 张扩展（2026-10-19）

- [x] T09 冻结 100 张、100 条本人未占用订单商品、有效身份及原始代码；规则基线与多图/人工分析分别保留。实际负例不冒充已确认漏关联。
- [x] T10 TDD 实现请求内知识库复用，验证嵌套、隔离、异常释放与新发布版本。
- [x] T11 TDD 修复字段语义及有界重读，保留原始观察与真实冲突。
- [x] T12 TDD 实现独立表面补证路径与无印刷平行名的软缺失复核，验证原有硬门槛。
- [x] T13 完成 100 张前后回放、人工负例及逐卡报告，区分离线升级和实际新增成本。
- [x] T14 跑相关 PostgreSQL/API/web 回归；发布网页与云端 API，断言 worker=true、健康及源码一致。
- [x] T15 检查个人 iPhone；已连接则覆盖安装验证，未连接则记录并安排待发布。
- [x] T16 TDD 修复实际多图候选被自身弱身份标记阻断，其他未复核候选继续阻断。
- [x] T17 定位生产公开画廊ReadError，有界修复并验证HTML/图片绑定与真实成本路径。
- [x] T18 TDD 修复四类真实格式失败与有界首轮JSON修复上下文，最终规则重算100例，失败例仅定向复读一次并保留阶段证据。

## 历史资料库四步优化（用户2026-10-19明确批准）
- [x] T19 单页完整购买历史读取与可恢复账号绑定游标，先失败测试再实现。
- [x] T20 私有原子资料库、多图下载缓存、独立商品文字资料及哈希失效。
- [x] T21 历史同步/商品准备/本地比对接口和CLI，完整性与账号/新鲜度保护。
- [x] T22 分段候选观察、跨步复用正证、非关键差异归一化；完整竞争域/财务/唯一性不变。
- [x] T23 完整100例正常服务验证及负例审计，核实实际成本差集和所有未完成项。
- [x] T24 审查、发布API/网页增量并核实worker=true；手机已连接则核版本/启动，未连接按用户授权记录延后，保留部署证据。
- [x] T25 真实793条资料重复评分定位、操作内纯评分复用；输入/版本/异步任务隔离、深拷贝、异常与商品文字不能授权成本回归，真实输出一致性基准及独立审查。
- [x] T26 复现并修复跨日续页错误刷新新鲜度、失效历史批处理回退旧数据库自动写账；过期部分扫描重启，失效任务保留进度，并验证原手工同步路径。
- [x] T27 真实服务剖析首轮重复身份/别名计算；按完整输入、知识版本与discovery模式在同操作内复用，验证隔离和可变返回值，793行完整输出对照及独立审查。
- [x] T28 真实100例发现整单范围误扩散；TDD改为逐商品待核验范围，保留丢文字、缺图、消失商品及无法还原的旧范围，审查后发布并重跑同100。
- [x] T29 真实多轮发现重复失败的打包模型请求；TDD同操作尝试去重，与纯值缓存隔离、输入变化/操作退出/子任务可重试；失败不产生结论或交易，审查后发布接续100。
- [x] T30 真实2/4条模型same观察因场域未完成被统一降级，影响待选列表；TDD保留逐候选same与同事务总闸，默认/自动确认持续阻断，只有明确人工选择写入；审查发布后按现行规则重跑100。
- [x] T31 真实长批次18例后容器OOM；TDD增加逐卡进程预算、真实账本断点续跑及未完成不出全量摘要，唯一驱动逐卡释放原生进程并采集RSS；保留900MiB、原18例和原账本，核实A019及后续100结果。

## 2026-10-20 视觉迁移（沿用本份任务）
- [x] T32 TDD迁移全部视觉调用到DeepSeek Flash非思考；分离保留联网搜索；验证型号/费用追踪、相关回归、真实图片与独立审查。
- [x] T33 提交并发布当前最新版API/网页/个人签名手机，验证真实视觉请求和worker=true，正常恢复原100断点并完成最终审计。

完成范围：2026-10-20原100例正常执行与最终审计完成，8/100有成本（原3+新增5），全部10例底层负例资格/待选/入账均0。T23/T28/T30/T31/T33表示已实现、部署并完成约定实测，不表示所有卡片恢复成本或全API测试全绿；详细局限见final-audit.md及validation.md。


## 2026-10-20 综合证据衔接优化（用户已批准方案及发布）
- [x] T34 冻结三卡与当前生产源码，RED复现标题已召回忽略商品图片字段及软差异被拒绝。
- [x] T35 GREEN实现可追溯多来源复核准入，保留底层冲突/缺失和最终身份门；当前绑定成对核图处理结构化字段疑错与真实图片冲突。
- [x] T36 完整竞争域重建旧待核验范围，缺图/消失/真实冲突及源变化保持阻断；测试缓存/操作复用边界。
- [x] T37 隔离PG及真实三卡、原10负例/100集分层验证，独立审查，真实成本和未知字段单列。
- [x] T38 提交推送、发布最新API/网页/个人签名手机，验证源/模型/worker/公网页面与鉴权，完成收敛和报告。
- [x] T39 实际855复跑发现旧占用单元无法核图、其他字段差异遮蔽明确卡号负证据；先RED再最小修复，只读排除different，same/unknown占用继续阻断，保留原分配；审查、发布并仅续跑855，完成真实入账/负例与最终审计。

Final checkpoint: 743 cost tests passed; changed-file Ruff passed; independent review resolved. Complete history 219 orders / 813 products (799 ready, 14 failed). Normal three-card stage plus ID855-only follow-up produced ID858 CNY6.08 and ID855 CNY6.20; ID861 remains unconfirmed. Both post-write reviews complete. All 10 current negative identity/offer/assignment gates zero; raw unverified recall is reported separately. Original100 transactions, assignments and identities preserved. API/web fusion2 and iPhone1.1.12(16) verified. Final evidence: validation.md, deployment.md, evidence-fusion-report.md. Original100 driver/automation stays stopped; no duplicated financial replay.

## 2026-10-21 User-authorized complete100 rerun
- [ ] T40 Execute the original frozen100 cards through the currently published normal cost path in a separate stage, preserving8 existing costs and all original evidence. Audit every new write and all10 negative gates; deliver a100-row summary with current cost/status, concrete blocker and source evidence for further human analysis. No forced selection or product code changes.

- [x] T41 修复明确采购来源与另一笔同款的归属冲突：真实故障归因、财务受阻/来源缺席/有效来源/无来源/直接确认回归；保留实际异常交易并单列；基于当前线上发布最小补丁并核对worker及Flash原生非思考，按第32张断点续跑，不把异常复核伪记为通过。
