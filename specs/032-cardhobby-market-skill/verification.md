# 验证与发布记录（2026-10-31）

## 实现

独立skill及六项云端MCP工具已实现：start_cardhobby_valuation、search_cardhobby_sales、inspect_cardhobby_candidate、review_cardhobby_candidates、get_cardhobby_valuation、list_cardhobby_valuations。目标无需入库；图片由对话模型识别和审核，云端保存真实请求、原响应、详情及图片SHA256并执行确定性过滤和统计。不存在另一个云端视觉worker。

0094仅新增chatgpt_market_run研究表。不写入持仓、交易、采购成本、正式估值或知识库。保留原OAuth scopes及现有购买查询worker。

## 测试

- 对新模块先运行缺模块RED；随后验证成交状态、日期、金额、未知运费、完整身份、三独立卖家、错误平行/品相、同图/同流水、前导零流水及缺流水。
- 真实一次性PostgreSQL manager_test_market验证幂等异参、跨账号、权限撤销、实际图片内容回传、过期审核摘要、并发搜索合并、搜索中撤销不发布结果。
- 独立静态审查找到并发详情快照混图、总页数缺失误判完成、出价次数未比对、捕获损坏仍可回读四项。补RED复现后修复；静态复核无剩余发布阻断。
- 真实官网商品页无人民币字符标签而通过CSS显示符号，首轮云端探测因此detail_verified=false。新增回归复现后，仅对同商品已捕获CNY搜索响应允许币种绑定，仍须金额/日期/出价次数一致。仅无币种、数字及固定出价文字单元格允许绑定；USD、CHF、SGD、KRW、新台币、원等显式/未知标签均拒绝。无绑定的原解析路径仍严格拒绝无标签价格。
- 最终针对性测试31 passed；扩大后的相关回归206 passed（11.74秒），一条既有Starlette弃用警告。ruff、compileall、git diff --check及skill quick_validate通过。
- 较大集合首次333 passed/35 failed；使用下载的原线上源码和隔离PYTHONPATH重跑相关旧模块得到相同35失败、无新增失败。旧失败位于原formal research/publication及provider匹配预期，未擅自修改。不能称全量测试全绿。

## 云端发布

基于原运行镜像0.6.10-1d21d784增量覆盖本次四个模块，镜像内部installed package与/app/src两份源码逐文件校验一致；未覆盖其他线上源码。最终镜像card-platform-api:0.6.10-20261031-market3，API健康，29个工具包含六个新增工具。仅应用0094迁移，未应用其他迁移。

运行容器实际purchase_cost_worker_enabled=true及环境true；视觉四路均deepseek-flash、thinking显式disabled；qwen-plus百炼搜索端点/密钥独立。环境、挂载及运行用户与发布前一致。原网页及OAuth元数据公网HTTP200。本功能没有网页/iPhone二进制修改，因此沿用既有客户端版本，不重复发布无关客户端。

独立ZIP仅含SKILL.md、agents/openai.yaml和两个references；个人Codex副本已安装。Chrome实际ChatGPT账号上传后显示“技能已上传”，已安装及由我创建列表均显示“卡淘成交估值”；云端详情可读实际技能内容及references。Skill ID=6ae60c10f9bc8191937757ad77852802。服务器与云端账号安装均已执行，非仅生成下载包。

## 真实检索与客户端

生产环境使用当前已授权账户进行只读研究验证：实际查询“库里 国宝”，第1页60条，源站1493条/25页，search_complete=false。商品145721478详情、正出价、过去结束时间与公开落槌原价一致；图库4张，实际首张图片2,057,560字节并留SHA256。原搜索capture_ref可回读；无目标图，报告estimate=null、ledger_modified=false。最新镜像重新执行同一流程通过，研究证据在云端持久卷。

另通过真实ChatGPT新聊天选择已安装skill和卡牌管家，模型自行调用get_profile/get_capabilities/start/search/get并返回真实源站记录与run_id；明确第3条召回其他球员、前两条同卖家，未把标题当图证。首轮图片请求在发布切换期间出现内部错误，模型如实报未完成并保留原run。稳定版本中恢复同一run、未新建或重搜，服务端audit记录inspect_cardhobby_candidate=ok及get_cardhobby_valuation=ok，原run图片捕获从0变1、detail_verified=true。

客户端最后回复“重试成功，已看到实际正面卡图”，正确读取STEPHEN CURRY、WARRIORS、NATIONAL TREASURES、TIMELESS TREASURES、48/99及深蓝织物窗口，未将标题年份认定为实图年份，未猜背面卡号或比赛使用属性，未凭照片评级。根聊天另读取同一公开图片核对，视觉内容一致。回读仍1次查询/60候选、reviewed_count=0、review_complete=false、estimate=null；如实区分详情核验与同款审核。真实客户端链路通过，但未冒称全部候选审核完成。

局限：本轮未使用用户提供的实卡正背样本完成非null真实估值，也未实际验收iPhone/Android技能选择与附件传递。不能将网页安装及单商品图片回读推定为所有手机端或所有版本识别准确率。模拟/合成用例只证明对应规则，不代表真实市场准确率。

发布材料与私有账户任务编号、客户端聊天引用及完整日志保存在Git忽略的.runtime/cardhobby-market-skill-20261031/，不进入skill包。回滚仅切回原镜像并保留worker=true及现有环境；研究表与证据保留。
