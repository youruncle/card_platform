# iOS 私人卡库第一阶段实施计划

**Goal:** Capacitor 手机客户端与现有后端打通登录、空库、照片任务、人工确认入库和私有图片，不迁移本地数据。
**Architecture:** 新增 apps/mobile（React、TypeScript、Vite、Capacitor 8），现有 FastAPI 增加移动会话和幂等任务接口。复用 scanner 处理流程，但移动任务必须人工确认，桌面自动入库行为保持原样。
**Spec:** spec.md；research.md
**Execution:** 本会话直接实施（writing-plans / executing-plans / test-driven-development）。用户已明确“开始执行”，不重复索要开工许可。

## 全局约束与决策

- 腾讯云沿用现有实例；不上传本地数据库、收藏、交易、照片或知识库。
- 当前 checkout 有大量相关未提交功能，另建 HEAD worktree 会丢失依赖；在当前 codex 分支增量编辑，仅交付本任务文件，不提交用户其他修改。
- 开发者账号和 HTTPS 域名尚待用户回答；完整 Xcode 未安装。可完成代码、Web 构建、接口和浏览器验证；原生编译与真机不虚报通过。
- iOS 凭证保存在 Keychain；Web 调试只在内存保留移动凭证。仅允许 HTTPS 服务端，开发调试仅放行 localhost/127.0.0.1。
- 不自动迁移卡库；云端联调仅限专用测试账户和合成样本。

## Review Focus

- 会话失效/退出后私有图片、草稿及旧异步响应不能显示在另一账户。
- 上传超时重试和重复确认不得创建重复任务/卡片。
- 桌面 Worker 当前自动入库，移动任务不能被其提前录入。
- 服务器返回外部图片 URL 不能将 Bearer 凭证发往外部域名。
- 模型不可用时任务失败可恢复，不能将占位字段当识别成功。

## Task 1: 后端移动会话与人工确认任务

Files: auth.py、auth_api.py、main.py、scanner_queue.py、新增 mobile_api.py、0077_mobile_capture.sql、tests/test_mobile_postgres.py。
Interfaces: POST /v1/mobile/auth/login 返回 token/expires_at/user；已有 current_user 同时支持 Bearer 与 Cookie。POST /v1/mobile/jobs 接收 front/back 与 Idempotency-Key UUID；POST /v1/mobile/jobs/{id}/confirm 接收 CollectionItemCreate，按任务行锁防重复。

- [ ] 在独立临时 PostgreSQL 写并运行接口测试，先验证移动端入口缺失导致失败。
- [ ] 提取登录共用验证逻辑，保持锁定、审计、撤销行为；不回退无效 Bearer 到 Cookie。
- [ ] 增加 require_confirmation 与 client_request_id/request_digest 字段、唯一约束；移动任务处理成功仅保存结果，确认时原子创建卡片与图片。
- [ ] 验证重复上传、内容冲突、确认重复、所有权、会话撤销、桌面行为回归。

## Task 2: 移动客户端与原生工程

Files: apps/mobile/package.json、capacitor.config.ts、src/{api,session,drafts,capture,App,styles}、ios 原生工程、tests。
Interfaces: MobileApi 封装同源端点与 Bearer；SessionStore 管理 origin/token/user；草稿按账户分区持久保存照片及请求 UUID。

- [ ] 写接口地址限制、凭证隔离、草稿与确认字段转换的失败测试。
- [ ] 实现原生 HTTP 与 Keychain 桥接；网页调试使用同源开发代理。
- [ ] 完成四入口、相机/相册、正反面预览旋转、上传重试、任务恢复、字段校正与确认、详情和鉴权图片。
- [ ] 生成 iOS 工程，配置相机/照片权限和隐私文件；不配置明文公网 ATS 豁免。
- [ ] 运行客户端测试、类型检查、生产构建、Capacitor 同步；浏览器验证真实本地 API 闭环。

## Task 3: 腾讯云联调与交付验证

Files: scripts/test-mobile.sh、移动端 README、specs 当前目录 tasks.md 与 verification.md。

- [ ] 只读核验现有云端与资源，准备可回退的增量 API 发布；仅传程序和本次迁移，不重打包含本地业务数据的包。
- [ ] 如接入条件具备，部署并用隔离测试账户验证；否则记录具体阻塞，交付可复现测试脚本与操作说明。
- [ ] 独立代码审查、修复重要问题、执行相关回归，逐项记录原生/真实模型/云端未验证项。
- [ ] 将后续邀请与 TestFlight 保留为下一里程碑，不宣称完整首版完成。

## 2026-10-07 已批准手机交互增量

沿用现有源码直接执行；整个 apps/mobile 尚未提交，避免另建仅有 HEAD 的工作树遗漏实际实现。仅修改手机端及本规格，保持 Web/API 兼容。

1. 用行为测试覆盖多草稿恢复、原子保存、旧上传不删除新编辑、配对顺序及尺寸换算；实现兼容旧草稿的队列存储与图片处理。
2. 用组件测试覆盖只有手机入口、配对调整、上传失败保留队列、成功推进下一张；实现批量选图、相机输入和手动裁切。
3. 验证鉴权图片重试、按需加载、App 内大图；增加持仓筛选及折叠证据。
4. 运行全部手机单测、类型检查、生产构建及浏览器手机尺寸流程；同步原生静态包。没有 Xcode/签名时明确保留真机验收，不宣称安装完成。

Ruling：当前双面识别接口不能直接省略背面；保留单面草稿并明确提示补背面，避免用重复正面/空白图片伪造证据。真单面识别留为显式未完成项。

用户补充后的先行步骤：提取 packages/card-core 纯类型契约和身份表单转换，Web/Mobile同时消费；保留两端网络与设备适配独立。为共享改动补齐两端构建与云端打包引用验证。不抽取视觉组件，不将扫描仪接口移入手机导航。

## 第二阶段实施计划（2026-10-07，用户已批准执行）

采用 subagent-driven-development 分工，原工作区增量开发，不提交或覆盖既有修改，不连接/发布云端，不迁移真实数据。共同依据 spec.md 第二阶段设计。

### Task 4 / T015：通用单面任务
- 文件：capture_api.py/capture_service.py（新增）、scanner_queue.py、scanner_api.py、mobile_api.py、schemas.py、0079_capture_sides.sql，test_capture_postgres.py。
- 接口：POST/GET /v1/capture/jobs，GET /v1/capture/jobs/{id}，POST /{id}/confirm、POST /{id}/retry；上传front/back至少一个，Idempotency-Key；返回既有任务字段（front/back URL允许null）+ available_sides、revision、target_item_id、images。每侧images描述至少含url/revision。兼容旧双面接口；worker按存在面处理。
- 补拍：上传可带 target_item_id、expected_revision（目标updated_at字符串）；所有权/版本检查，确认更新同一卡片已提交的图片，不能覆盖已确认身份字段。完整字段差异确认作为用户显式提交才应用。
- 先红后绿：单/双面、空输入、缺失侧、重复/冲突、补拍、旧接口、跨账号、worker单图识别。临时PG端口55440。

### Task 5 / T016：全库排序
- 文件：portfolio_sort.py（新增）、api.py分页路由、repository.py必要投影字段、web排序控件与调用、shared排序模块、tests。
- 接口：items/page 增加 sort_by=created_at|player_name|season_start|remaining_cost|fair_value，order=asc|desc，snapshot_token可选，snapshot=true开启首次快照；响应附snapshot_token/snapshot_at。无新参数保持默认顺序。
- 复用repository最终投影，对全候选集排序再分页；Decimal、未知值最后、可比币种、稳定ID次序；TTL60s、owner/query绑定、删除权限、有限缓存。
- 网页消费同一排序契约，切排序重置第一页。先红后绿，PG测试端口55441。

### Task 6 / T017：裁切和私有衍生图
- 文件：capture_media_api.py/image_variants.py/crop_proposals.py（新增）、相应tests。不改main.py，由主线程注册。
- 接口：POST /v1/capture/crop-proposals (image文件)，返回 proposal_id/source_hash/source_url/preview_url/quad/confidence/width/height；POST /{id}/apply 接受quad|null（null保留原图），返回裁切图URL；私有GET资源可供两端预览。提议24h到期，owner绑定，四角统一TL/TR/BR/BL 0..1。
- GET /v1/portfolio/items/{id}/images/{side}?variant=thumb|preview|original&revision=...，返回私有图片，尺寸320/960/原始；HEAD一致权限，源版本与当前图片绑定，原子写入、并发限制。任务图片的同一衍生生成器可由capture服务复用。
- 主线程在两端选择缩略图地址，原图字段保留。先红后绿测试安全/变换/版本/重试，PG端口55442。

### Task 7 / T018：两端集成及iOS取景
- 主线程文件：apps/mobile、packages/card-core/src/capture*.ts/types必要适配、Web图片组件（避开排序修改）、main.py注册、client_capabilities.py、iOS原生桥接。
- Mobile支持单面模式、补拍入口、源图+配方、服务端裁切提议与人工四角修正、媒体尺寸请求、排序与快照、能力降级。
- Native薄桥接提供相机实时矩形提示与手动快门，按Apple框架实现源代码；原生SDK不可用时明确未编译、未真机验证。保留系统相机降级。
- 共享API契约不引入平台副作用；服务器权威业务规则不复制到前端。独立模块测试、类型构建、临时PG浏览器流和最终代码审查。

### Task 8 / T019：集成审查及交付记录
- 分模块测试+两端完整测试构建，临时数据库运行新旧接口/权限回归，合成样本浏览器流程；真实模型和真实卡裁切未验证须标注。
- 校验打包包含新模块，所有数据库迁移仅在隔离库执行。记录回退条件、性能实测与未满足的验收，不以cap sync当原生编译。
