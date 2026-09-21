# 卡淘估值质量优化 Implementation Plan

> 后续执行者读取本目录 spec.md 与 tasks.md；用户确认后按 Superpowers executing-plans、systematic-debugging、test-driven-development、verification-before-completion 执行同一任务清单。2026-09-21用户已批准执行现有任务和达标后直接批量写回。

**Goal**: 增加有可信同款证据支撑的合理估值，降低错卡及偏价。  
**Architecture**: 保留现有识别、market_data、正式同款门禁、估值器；增加可回放的审计记录，在只读影子环境逐轮验证最小修改。  
**Tech Stack**: Python≥3.12 / FastAPI / psycopg / PostgreSQL；现有Next.js界面本阶段不改。  
**Spec**: [spec.md](spec.md)  
**Branch**: 实际工作分支为codex/cardhobby-valuation-quality；Spec Kit逻辑feature为002-cardhobby-valuation-quality。  
**Date**: 2026-09-21；**Status**: 实施中，验收未通过。

## Summary

采用research.md的推荐方案。R0冻结与抽查已经实施；完整60张独立参考和成本性能基线仍有缺口，在R1补齐。R2优先修已证错卡/身份，再做R3召回；R4只在充分参考集上校准，全部验收通过后按用户授权直接批量发布。

## Technical Context

- 存储：生产只读重复读事务；私有原图、候选快照和原始JSON保存在忽略的work/audits/valuation-design-20260921。仓库只保存脱敏结论、采样规则及只读导出工具。
- 校验：pytest纯单元测试、真实冻结样本纯函数回放、影子评估；数据库集成测试只在新建的本地隔离PostgreSQL运行，生产连接始终强制只读。
- 范围：519张机器盘点，固定60张分层集，初始14张定向复核，实施阶段补至固定60张原图/可获取候选复核；3373条冻结卡淘候选及新检索响应留存。
- 性能：按spec SC-006，先建立同预算、同缓存条件的完整新基线，旧审计写入时间不作SLA。
- 无新产品依赖、无数据库迁移、无部署。后来如需持久trace表再在本计划更新，不隐式添加。

## Global Constraints

仅卡淘；不改生产估值/身份/任务；不买入价补值；不以门禁放宽增加覆盖；真实证据不够则unknown。保留全部未提交修改，仅改任务相关文件。spec/plan/tasks唯一事实源，R1–R4已批准执行；未达验收标准不得正式写回。

## Constitution Check

| 原则 | 研究前/设计后核验 |
|---|---|
| 现有架构与成果 | 保留Next.js/FastAPI/PostgreSQL，文件哈希核对；从原工作区建立任务分支、不提交既有工作 |
| 识别可追溯 | 原图/候选/身份版本哈希；字段角色、未知和冲突独立保存 |
| 验证原症状 | #97/#135/#383/#486有原图或纯函数复现；正常对照同集 |
| 隔离 | DB强制只读，公开文档不含账户UUID、密钥和采购内容 |
| 真实证据 | 明确落槌≠付款；参考不足时null；不使用采购成本 |

均符合，无需要豁免的原则。当前资料不足的是参考标签与成本实测，作为阶段退出条件，不通过放宽原则消除。

## Project Structure

- `specs/002-cardhobby-valuation-quality/`：本方案、research、samples、sample-reviews.json、sample-set.json、baseline-summary.json、data-model、quickstart、tasks、checklists。
- `export_baseline.py`：本目录内只读SQL导出；不复用会写状态的worker.run/_calculate。
- `work/audits/valuation-design-20260921/`：冻结私有数据、图片、页面、验证日志及保留检查。
- 后续源码范围：`apps/api/src/card_platform/{image_service.py,vision_routing.py,valuation_worker.py,comparable_image_verifier.py,formal_cardhobby_gate.py,marketplace_aliases.py,domain/player_aliases.py,domain/card_identity.py,domain/valuation_quality.py,market_data/cardhobby_provider.py,ingestion/cardhobby.py}`。只按任务选择必要部分，不全面重构。
- 后续新增小模块：`valuation_trace.py`承载可选无业务副作用的trace sink；`scripts/audit_cardhobby_valuation.py`负责离线/只读shadow运行。现已实现基础入口，完整七阶段与最终合格证据驱动停止仍未全部满足，见verification.md。

## 分轮实施及退出条件

### R0 — 本轮只读评估（现已完成可访问部分）

冻结账户、代码状态、身份来源、旧证据；全量分层；14张原图/候选直接核验；七段代码分析与181个现有单测验证。当前参考区间均无法充分确定，未报告价格准确率、模型成本改善或已优化结果。交付本目录供用户确认。

### R1 — 可回放与观测基线（确认后）

US1：实现只读审计入口和trace事件，显式禁止生产写入；离线模式不联网，shadow模式仅新增本地证据文件，网络预算可配置。逐候选、逐query、逐模型记录elapsed/usage/cache/retry/truncation；认定null不等于0。身份实际解析与生产共享纯函数，不能复制产生第二套不同规则。

US2：补齐60张复核，优先取得商品详情/背图，缺失仍标unknown。由看不到当前系统价格的复核者先标身份与来源资格，再生成区间，最后解盲。至少3个不同商品ID、至少2卖家且90天内并排除可见重复实卡才能标充分参考；不足保留历史观测。

退出：基线可复现；新调用trace覆盖100%；60张均记录完成/受阻；可判定数和未知数冻结。不能补齐来源时交付受阻清单，不进入价格校准。

### R2 — 身份/同款最小修复

US2：先用#97清单区域、#486GR-5与基础卡、#383星形纹理写失败测试。补字段provenance和纹理冲突判定；旧same必须绑定图像与身份版本。对#135同形异码及中文名建立实体审核证据，先用于检索，不能对任意语言全局转写或当作身份真值。#4坏背面继续未知，不靠候选反填原卡身份。

退出：已证反例全部正确拒绝/保留未知；正常图像反光对照不误杀；不修改生产卡片字段。影子结果与基线逐卡比对，记录新增拒绝及新增通过原因。

### R3 — 提高合格证据召回

US3：基于相同冻结候选输入修正查询顺序、分页与提前停止，停止依据是最终合格且去重的同卡证据数。明确页数/词数/图像预算与达到上限原因；新增结果必须经过同样门禁，不得把标题score或检索别名当同款确认。#102保留已召回被拒候选以定位漏斗；#135新来源作召回正例、评级/橙折/微瑕作反例。

退出：同预算A/B记录每卡新增有效证据、恢复覆盖、错卡、成本和P95；SC-003/005/006通过；不足则缩小改动或继续调查，不批量发布。

### R4 — 价格校准与发布决策

US3：先隔离单证据、低价1次出价、近期/历史、品相及重复实卡，再比较现估值器和最小参数调整。不引入无证据估价模型。参考标签不足20张时不调优总体参数或声称普遍改进。

退出：保留集满足SC-003/004/005/006，逐条新增估值可追溯；人工审阅风险清单；保存逐卡前后对比及回退清单后，全部已确认验收标准通过则直接批量写回；未知指标和缺证据卡不视为达标。

## 指标定义

固定N=519报告有价比例；固定抽样集报告分层结果，不直接外推总体。D=充分参考卡集合，U=无法判定集合，D和U始终同时报告。合理覆盖=在D中有估值且价格落在独立参考区间内的卡数/|D|；新增参考卡不改变冻结版本的比较分母。

价差d(v,[L,H])：v<L时(L-v)/L，v>H时(v-H)/H，其余0；高估/低估需超出区间且d>35%。仅在充分参考标签上定性，单证据跨度不用于判错价。另报告估值区间与参考区间交并比、系统区间宽度/参考中点（避免无限放宽）、未发布率。

错卡引用率=独立判为异卡或评级/版本冲突的已采用来源数/已复核采用来源数；unknown单独列、不当正确。分页收益=新增最终合格唯一商品数/新增query成本。所有比例携带n与分层，不把181个单测通过当业务准确率。

## Review Focus

1. OCR清单号/球衣号/统计年误当身份：T008。
2. 固定星纹与拍摄反光、同限编不同卡混淆：T009。
3. 请求失败伪装零结果、早停后下游全拒绝：T006、T012。
4. 状态=-2无正出价/挂牌/详情缺失/模板免责声明：T010。
5. 1元低竞价、重复上架同实卡、运费误计与未知成本：T005、T014。

具体任务、依赖、验证命令见tasks.md和quickstart.md。详细实现仅在用户确认后按任务展开，避免本阶段把方案写成已实施代码。
