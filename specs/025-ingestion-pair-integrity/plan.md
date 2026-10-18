# 录入正反面完整性 Implementation Plan

> **For agentic workers:** Execute the same Spec Kit tasks with systematic-debugging, test-driven-development and verification-before-completion; independent modules may use dispatching-parallel-agents. Do not create a second requirements/plan set.

**Goal:** 恢复后的库存保持证据可追溯，并阻止同批错配、跨批混图及双正面再次静默入库。

**Architecture:** 复用一次双图视觉识别生成逐面观察和绑定图片的配对结论；共享服务器门禁管住自动和人工确认入口。平板匹配分离可靠身份信号与几何位置；确认冻结源区域与版面版本，界面显式区分当前批重扫和新批次。

**Tech Stack:** FastAPI/Python/Pydantic、PostgreSQL、Next.js/React、Capacitor/iOS。

**Spec:** `specs/025-ingestion-pair-integrity/spec.md`

## Global Constraints

- 保留用户未提交修改；在隔离worktree中以当前工作区快照为基线，只回迁本次增量。
- 保留所有权隔离、历史源图、人工交易；不要覆写共享卡定义修复别的库存。
- 生产`PURCHASE_COST_WORKER_ENABLED=true`，部署和回滚保持开启并在容器内实测。
- 无新增单面付费模型调用；模型缺字段/失败不得被解释为已核验一致。
- 低裁切质量和普通低识别置信度仍为提示；确定身份冲突与重复正面是阻断条件。
- 正常多主体、单面模式、确认幂等及当前iPhone签名渠道保留。

## Review Focus

- OCR只返回年份、OCR完全缺失、同球员不同产品及同款实体歧义。
- 模型说consistent但逐面证据冲突；缺新字段、图片替换后旧审核、客户端省略审核字段。
- 双主体Generations/Impulse及Topps/Bowman同厂商标识不能被错误硬拒绝。
- 确认期间补扫、跨批替换、旧请求迟到、重复确认及所有权不同。
- 旧客户端错误可读，失败草稿无库存/成本/估值副作用；线上worker和手机实际版本验证。

## Task Interfaces

### T1: OCR和匹配

Ownership: `flatbed_matching.py`、新增`flatbed_pair_signals.py`与相应纯单测。产出`extract_pair_signals(text: str)->dict`，原`FlatbedService.ocr_signals()`由T3改为调用它。信号键保留兼容`ocr_text/ocr_tokens`，新增可靠`product_name/insert_name/player_name`和明确赛季`season_start/season_end`；普通日期不写`year`。`propose_pairs()`保留现有键，可添加identity状态和歧义提示。

先写失败测试覆盖657年差、同球员异产品、缺OCR、普通正常卡及近似版面，再实现不重复OCR加权的评分和匹配。用真实冻结信号回放，不能只验证手工构造样本。

### T2: 逐面识别和共享入库门禁

Ownership: 新增`pair_integrity.py`、`schemas.py`、`image_service.py`、`scanner_queue.py`、`capture_service.py`及其测试。使用`CardPairValidation`：`status=consistent|conflict|unknown`，`reason_codes/evidence`数组，`front/back`逐面观察，`image_content_hashes`、`version`。`CardRecognitionResult.pair_validation`为可缺省字段；服务器绑定当前图片哈希并校验，不能相信客户端的状态。

共享接口`validate_pair_for_confirmation(job_result: dict, image_hashes: dict, review: dict|None)->None`：consistent要求哈希匹配；conflict拒绝；unknown要求`pair_review={confirmed:true,image_content_hashes:{front,back}}`。双面未知/冲突自动任务保存为succeeded审核草稿并require_confirmation=true，不创建库存或队列。明确单面任务保留。

先写失败测试覆盖模型显式冲突、双正面、同主体异产品、合法双主体、缺字段、未知显式审核、图片更换、重复确认和无副作用。修改现有一次双图提示并处理字段，再接两类入库入口。新增字段传至capture/scanner视图（若T3负责客户端类型需提供准确契约）。不得编造一致结论来让旧测试通过。

### T3: 扫描来源冻结与客户端审核

Ownership: `flatbed_service.py`、`flatbed_api.py`、`scanner_api.py`（必要时）、迁移`0083_ingestion_pair_integrity.sql`、web/mobile客户端及测试。服务器会话锁+confirmed_sources JSON记录确认时的sheet IDs/revision/SHA和selected region IDs；重新扫描/裁切/生成配对清除确认。提交重新核对，旧快照返回409；所有权和原有质量提示保留。

界面明确新批/重扫当前批，去掉提前换批文案，显示源版本和配对结论。网页识别草稿使用服务器capture confirm（使body携带`pair_review`）而非先createItem再删除识别任务；手机对unknown显示当前两图及独立确认勾选，对conflict显示修正/重拍入口。capture confirm从body提取`pair_review`后保留原身份/交易校验，不扩展job状态枚举。

先写失败API/浏览器/手机测试覆盖确认来源改变、旧确认、模型未知草稿、已知冲突不可绕过、单面正常与幂等，然后实现。

### T4: 集成验证与发布

复核T1/T2/T3的同一增量，运行针对单测、隔离PostgreSQL迁移及实际样本回放；web/mobile相关测试和构建。已知既有失败逐项记录，不能宣称全量通过。确认数据恢复21条和手工110元不被版本更新回退。

发布本机扫描API、云端API+网页、手机新版本；API能力声明新增配对审核支持。云端先备份镜像/Compose及数据库，再应用兼容迁移、健康检查后切换；回滚只恢复镜像字段，worker保持true。iPhone覆盖安装读回版本、启动。记录真实模型样本数量、失败/未知及未覆盖边界。
