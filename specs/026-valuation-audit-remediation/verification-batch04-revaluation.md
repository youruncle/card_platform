# 第四批新版重估与质量验收结果

截至 2026-10-19 15:51:13（Asia/Shanghai），固定200张的执行、71张原撤价人工复核及通用缺陷修复/发布已完成。**未证明新增可信估值或数值纠价改善，不能计算数值准确率。**已证明的改善是：持仓本件历史不再进入独立市场计算或触发提前停核，四个不受独立证据支持的新价从市场/影子展示撤下；已发布的C.J./CJ别名匹配接通。没有可信替代价，也没有把null计作可信纠价。

## 固定范围与运行结果

范围仍为 `work/audits/valuation-batch04-20261019/batch-04.json` 的200张，09:29提交的原映射任务没有重复入队。最后77张和其他批次未启动。

| 项目 | 最终结果 |
| --- | --- |
| 原200任务 | 200终态：4历史成功、196证据不足、0失败；运行/排队0 |
| 系统同卡后继 | 13项全部证据不足；与原200分列 |
| 本轮相关刷新 | 222项终态：178成功、44不足；无运行/排队 |
| 基线 | 149旧价、51无价 |
| 当前展示 | 74旧历史价保留；75原有价变null；51原无价仍无价 |
| 75次撤价组成 | 原重估71张，加本次读取合同撤下的4个新本件价 |
| 新增可信估值 / 可信纠价 | 0 / 0 |

74个旧历史价不算本轮成功。四个succeeded任务和四份新快照原样保留，读取层不把它们当作可发布的独立市场价；任务状态与展示状态分别记录。全部200个既有独立参考low/fair/high均null，不能形成数值准确率分母。213项新任务均为缓存回放，任务内执行查询数为0；后台刷新另计，缓存列表长度不算新搜索或成交数。

## 71张原撤价的实际质量复核

既有6张加本次65张已覆盖全部71张。新增65张实际查看409张目标/完整商品图库图、核135个旧来源绑定及原HTML，逐卡绑定旧快照、原任务、最新显示/后继和图片SHA；广告、角度图、同图多URL分别识别，不当作独立成交。来源索引没有代替人工核图。

新增65张中，368/451/464/485/538及397/457/529/533共9张的全部旧锚点存在明确版本冲突；374/544另有唯一锚点的版本或品相不足。混合错版与相容来源逐条分开，不能由部分坏源推导整个旧价数值错误。397等当前冲突与人工核图相符；多张仍仅为stale/paired/finish待核或时限耗尽。536/541及351/383/392/394/406/480/504/519/531等缓存理由与可见图片不符，另有背图存在却称缺背、反光当固定工艺、条码/说明数字当卡号/流水的问题。原始模型响应缺失时只记录结构化缓存的可见错误，不编造模型根因或调用次数。

所有71张旧价数值错误仍未独立证明，有效价损失未知。#493只发板不发码的旧源不能支持完整兑换权益，但旧候选不在新拒绝清单，不能称新版明确识别并修复该问题。#495正面077/299而持仓仍为17/299/缺卡号；#517图像216/399而持仓/模型276/399，均未人工改生产身份。四张513/410/436/463存在VisualSchemaError诊断，重复诊断不等于失败调用次数；114个视觉时限案例保留，未通过放宽same/提示或加并发制造成功。

## 四个新价与通用修复

| 卡片 | 持久新快照历史金额（CNY） | 当前市场/影子展示 |
| --- | --- | --- |
| 516 | 5→4 | null / null |
| 490 | 35→64.32 | null / null |
| 507 | 8→7.99 | null / null |
| 517 | 30→70 | null / null |

四条来源均通过当前owner/item的assignment、purchase unit、buy transaction和evidence_hash只读联结确认为持仓本件历史。T0/T1身份准入不等于独立市场金额，品相与独立市场区间不足。原快照、历史金额、图片、来源及成功任务没有被删除或手工修改。正式计算与visual停核使用同一独立性过滤；T2流水/证书/寄卖键保持版本隔离，只有已核同主图可传播本件关系。读取合同同时覆盖 `used_comparables` 和旧正式 `comparables`，跳过明确未参与中心/零权重上下文，撤下污染fair和shadow并阻断联合估值从旧原价复活；手工价、已标历史Codex参考及当前queued/failed错误诊断保留。

#444实际当前缓存及已发布知识v18的只读回放，修复前C.J.未接通CJ中文别名，修复后两条来源三人均匹配；没有放宽视觉same/finish，也没有重入队。该卡正式证据仍因paired/finish/stale/视觉待核等原因不合格，未生成可信价，缺失的原始模型响应仍不可追回。

币种结论也已纠正适用域：原商品页主价没有币种符号，不能用DB默认CNY自证；但已捕获[官方搜索模板](https://www.cardhobby.com.cn/market/search?ckey=0&keyword=)、官方Vue脚本及API路由映射，确证中国站 `SearchCommodity.LowestPrice` 主值使用￥，约USD和运费另列。现有provider恰读取该字段，因此不作无依据的全量未知币种降级；这不证明拍卖已付款，也不把任意HTML/其他来源币种默认为CNY。未新增Codex双市场数值研究。

## 验证、发布与局限

红→绿覆盖四个真实本件载荷、停核继续、不同流水/证书、T2跨版本、同图传播、错姓/缺人及日文/变音别名、两种快照形状、unused上下文、当前状态/错误及真实SQL shadow联结。新增及真实控制46通过，相关169通过；隔离PostgreSQL14通过；合回原工作区后56通过；独立最终聚焦127通过。最终冻结源码完整API：158020通过、134失败、250跳过；134失败与修改前逐名完全相同，新增失败0，不能宣称全套通过。目标lint6项既有问题（含旧 `resolved_identity` 未定义）未在本范围修复，新增lint问题0。未执行新的外部视觉模型调用或手机回归。

15:43:36 API、15:44:05网页切换至valuation-quality1；API镜像SHA `f1502e2489b01a5c8823657ce47fecddd9707e53f4c2d8d9e017fb6b6e3444f1`。以切换时实际history10为父镜像，仅叠加5份估值相关源码，其余182份父源包括并行成本发布逐字节保留；最终187份源码SHA及formal v22/visual v9/number-roles4通过。成本worker实际持续true，卡片3/图片6/DB8、来源并发1；403/429/挑战0，无OOM/重启。网页首页、player-prices及静态资源、API代理检查通过；15:46最新health/ready HTTP200约22ms/2ms。手机无源码改动，本次没有重复安装。

最终完整只读快照：`state-final-20261019T074534Z.json.gz`，SHA `6b65c7cfcd76d1935d238148eab2c73c6c705707a496759040b6c033226a26c9`。发布前后213个本轮任务ID/状态及全部存储快照相同；只改变四张投影价。7291份冻结文件（2393305606字节）、completion manifest及两份基线SHA全部未变。原过早关闭收据/报告/JSON/CSV原文仍在prior-closure，未改写历史证据。

automation-4“新版重估进度与改善核验”保持PAUSED，未修改其他automation，也未停用生产成本worker。验收完成指本次范围的核图、缺陷修复与发布验证完成；上述识别/缓存/模型问题和数值真值未知明确保留，不等同于全面估值质量改善。

## 证据入口

- [同一逐卡JSON（200张）](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/final-card-evidence.json)；[同一逐卡CSV](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/final-card-comparison.csv)
- [71张人工覆盖与65张新核图SHA](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/manual-quality-coverage.json)；[32张分项](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/withdrawals-a/summary.json)；[33张分项](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/withdrawals-b/summary.json)
- [独立审查](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/independent-review.md)；[完整API基线逐名比较](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/final-api-comparison.json)；[隔离SQL及真实shadow检查](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/projection-confirmed-shadow-green.log)
- [发布后ID/快照/投影守恒](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/post-release-invariants.json)；[源与成本发布连续性](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/quality1-release-continuity.json)；[官方币种适用域与捕获](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/currency-contract/assessment.json)
- [最终完整快照](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/state-final-20261019T074534Z.json.gz)；[原关闭证据归档](/Users/song/Documents/Codex/2026-07-19/w/card_platform/.runtime/revaluations/20261019-batch04-valuation7/continuation-20261019/prior-closure)
