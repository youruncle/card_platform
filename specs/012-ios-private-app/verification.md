# 第一阶段验证记录

记录日期：2026-10-06。范围：功能联通，非完整首版/真机交付。

## 已实现

- 新增 apps/mobile，四入口、服务地址校验、登录与会话恢复、卡库搜索分页、详情与成交证据、正反面上传/预览/旋转、草稿持久化、任务轮询/恢复、失败重试和人工确认入库。
- 新增 /v1/mobile/auth/login、GET/POST /v1/mobile/jobs、POST /v1/mobile/jobs/{id}/confirm；复用现有可撤销 Session 的 Bearer 鉴权。
- 上传以 owner + request UUID 唯一约束和事务锁防重；不同照片复用同一编号返回 409。确认以任务行锁防重。
- 手机 Worker 仅保存识别结果，需人工确认；桌面扫描自动入库逻辑保留。
- Capacitor iOS 工程、Keychain 插件、原生网络适配和相机/相册权限说明。原生网络禁止重定向；退出与账户切换释放私有资源。

## 运行证据

- `scripts/test-mobile.sh`：23 passed。临时 PostgreSQL，原图/展示图为合成样本；验证会话撤销、越权、私有照片、重复和并发重复上传、人工确认与重复确认、手机/桌面任务分离及现有扫描单测。
- `npm test --prefix apps/mobile`：8 passed。地址/凭证隔离、401 处理、草稿账户隔离、旧上传不删除新草稿、退出后的迟到写入、必填身份确认、原生 multipart 序列化及超时/重定向选项。
- `MOBILE_BROWSER_CHECK=1 scripts/test-mobile.sh`：手机尺寸 Chrome 完成登录、空库、选取正反面、上传、任务查看、人工确认、详情与鉴权图片、退出。外部模型结果是明确标注的测试边界样本；真实 HTTP、数据库、文件和界面参与验证。
- 前端 TypeScript 与 Vite 生产构建通过；Capacitor iOS 同步通过。
- Swift `-frontend -parse` 与 Info.plist 检查通过，仅证明语法/文件格式，不证明 iOS 类型检查、链接或运行。
- 全后端套件：157404 passed、1 skipped、1 failed。失败：`test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[None-error2-failed]`。现有卡淘 provider 全部查询失败时抛 RuntimeError，而测试仍期待返回结果；涉及已有未提交的卡淘逻辑，本任务未修改该模块。单独复现结果另见本地日志，不将全套报告为通过。

## 腾讯云

已通过 SSH 核验原有服务健康、约 4GB 内存；未新建服务器。仅发布 6 个 Python 程序文件、main.py 的 router 接入和 0077 增量迁移，不上传本地卡库、图片、交易、知识库或环境文件。

部署前校验源文件哈希，保留代码包与旧镜像；本次服务器回退目录：`/home/ubuntu/card-platform/backups/mobile-api-20261005T204323Z`。日期为服务器 UTC。回退时恢复其中 source.tar.gz 与 image-tag.txt 对应镜像，再重建 API 容器；新增数据库列与索引可保留，勿清空数据卷。

`scripts/mobile-cloud-smoke.py` 在 API 容器的回环地址测试，注册继续关闭。通过：移动登录、空库、重复上传防护、私有图片和账户隔离、未配置模型的明确失败、退出撤销。两个临时账户及生成的任务图片已清理，收藏数量检查未发生变化。

## 独立审查与修复

独立 reviewer 只读审查，无后端阻断项。以下均已修改：
- SceneDelegate 使用自定义控制器注册 Keychain 插件；目前仅 Swift 语法验证，仍需原生构建验证。
- 旧上传完成时按 request UUID 条件清除草稿，避免删除新照片；回归测试先失败后通过。
- 照片处理完成前检查组件生命周期，退出后 DraftStore 拒绝迟到写入；回归覆盖关闭后的写入。
- iOS 网络库优先取 connectTimeout：上传的连接/读取超时统一 180 秒；测试先失败后通过。
- 浏览器联调发现默认 fetch 被作为类方法调用导致 Illegal invocation；改为平台请求包装函数，浏览器闭环验证通过。

## 尚未验证 / 外部条件

- 云端仍是 HTTP；缺少已确认的 HTTPS 域名/证书。原生客户端拒绝公网明文 HTTP，没有为绕过条件而放开 ATS。
- 云端 VISION_API_KEY 和 AI_API_KEY 未配置；真实识别、联网估值、模型成本和真实照片准确率未验证。
- Mac 没有完整 Xcode/iOS SDK；未进行 iOS 编译、签名、模拟器或 iPhone 真机安装。Apple 开发者账号状态尚未提供。
- HEIC 原始文件保留、原生相机方向、Keychain、弱网内存/生命周期行为需真机验证；当前只保留传到服务端的 JPEG 与处理证据。
- 尚未完成朋友邀请、TestFlight、20 组真实样本、并发容量、异机备份恢复验收；这些是后续里程碑。

## 复现资料

本地日志与浏览器截图位于 work/mobile（git 忽略，未包含真实卡库）。测试截图只含生成的卡片。代码使用当前含未提交成果的 codex 分支增量实现，未提交、覆盖或清理用户其他修改。

## 2026-10-07 手机交互和两端共享改造

用户确认先开发功能，HTTPS/Apple 账号之后补充；本次不发布云端，也不修改真实卡库。沿用当前未提交的手机项目，不重建第二套应用。

已实现：
- apps/mobile 的相机/相册输入、多图配对顺序调整、多卡草稿、交换正反面、旋转和手动裁切；不提供扫描仪入口。
- 长边 2560 像素的等比上传副本；设备原相册不改动；格式不支持时明确提示。
- 按账户隔离的草稿队列，兼容旧单草稿；上传失败保留同一请求ID，成功只移除相应草稿。
- 私有图片接近可见范围时才请求、失败重试、App 内大图/缩放；持仓筛选、折叠证据、只读知识查询。
- packages/card-core 提供接口契约、搜索解析、身份字段转换。两端直接引用同一份源；Web原有类型导出和搜索模块保留转发兼容。
- Web手工入库与Mobile任务确认共用身份转换；取消签字标记时统一去除签字类型和题词，明确指定的赛季结束年不被旧识别覆盖。
- 更新两个Web Compose构建上下文、Dockerfile专用ignore、空库打包脚本；隔离临时目录执行打包已确认包含共享源码和ignore。此包未上传服务器。

独立审查发现并修复：旧录卡页面上传完成后，新页面可能持有过期队列并将已上传草稿写回。新增草稿变更订阅、快照冲突检查和跨页面延迟上传回归；先复现失败再修复。

验证范围：
- Web 108 项测试通过，生产构建通过。
- 手机浏览器 390×844 流程通过：登录、批量3图、重新配对、缺背面禁止提交、切换页面恢复草稿、裁切输出宽度400→380、上传、确认测试卡入库、鉴权图片、大图缩放、知识只读查询、退出。
- 浏览器使用隔离临时PostgreSQL、合成图片；识别结果明确以测试夹具注入，不代表真实模型识别效果。测试服务和临时数据库由脚本退出时关闭。
- 裁切和详情截图已查看；见 work/mobile/crop-phone.png、detail.png、viewer-phone.png。

仍未验证/未完成：真机相机和HEIC解码、双指缩放手感、原生HTTP、签名和安装；本机只有Command Line Tools。无Docker可执行文件，因此未实际构建Linux容器，仅验证Next构建和打包文件。真正单面识别、全库排序、服务器缩略图及自动取景裁切尚未实现；保留单面草稿，不用重复正面/空白背面冒充双面。API共享契约仍为人工维护，没有自动OpenAPI生成。

最终补充验证：手机全套 22 项测试通过，TypeScript/Vite生产构建通过，Capacitor静态资源同步通过。新增过期快照回归还发现被拒绝的写入会阻止下一次读取；已使读取等待写入结束但保留读取上次成功数据的能力，失败仍反馈给写入调用者。此前该回归失败已修复并通过全套测试。Web最终108项测试及构建通过；未修改后端代码，没有重跑完整后端单测。完成差异空白检查及打包脚本Python语法检查。未提交、合并或发布；保留原有未提交工作。


## 第二阶段实施验证（2026-10-07）

本节覆盖历史记录中“尚未实现”的四项能力；本次只改本地代码与临时数据，未迁移真实卡库、未发布云端、未创建提交。

### 已实现的共用业务

- `CaptureService`、通用`/v1/capture/jobs`、单/双面worker与人工确认；补拍带目标版本，确认更新同一卡片，默认保留人工身份；旧双面DTO与路由兼容。迁移为`0079_capture_sides.sql`，避开工作区已有0078。
- 两端共享 `card-core/{contracts,capture,sorting,supplement}`；身份字段、面别/草稿表单、图片尺寸、排序/快照、差异接受规则不在两个客户端各写一套。
- 四角提议/人工调整/保留整张；源图哈希与副本保留，组合裁切和旋转从源图重建，已确认输入不再次自动裁切或判向。评级盒选项默认整图，要求核对标签。新入口在OpenCV解码前校验40MP。
- 全库最终展示投影排序后分页；60秒快照，账号最多3份，全局默认32MiB；金额Decimal、不可比/缺失值放最后、稳定ID次序。表格和手机都显示快照时间。
- 私有320/960/原尺寸图片、按需加载、503有限重试、GET/HEAD鉴权、版本失效、单进程合并生成、原子文件写入。24小时提议与7日衍生文件按需清理，衍生缓存512MiB配额。
- iOS AVFoundation手动快门与Vision限频取景提示、取消/后台关闭、临时照片读取后release；相机帧不上传服务端。浏览器与无原生插件路径仍可用系统输入与拍后提议。

### 初版身份修改边界

补拍始终可选择只补图片。差异接受仅开放球员、品牌、赛季、产品/系列、卡号、特卡和平行等主身份字段；默认全部未勾选，未知不作为清空操作，铭文/签字/物料/印量等未选字段完整保留。已有身份审核版本、正式市场绑定、估值/分析快照或正在运行估值的卡不能通过补拍改身份，需使用现有身份审核入口，防止旧确认身份继续影响估值；接口和两端界面同时限制。目标定义存在其他字段冲突时返回409，不修改共享定义或另一张卡。

### 验证证据

- 临时PostgreSQL组合回归命令：`CARD_PLATFORM_TEST_PORT=55439 bash scripts/test-mobile.sh apps/api/tests/test_capture_postgres.py apps/api/tests/test_capture_media.py apps/api/tests/test_capture_media_postgres.py apps/api/tests/test_portfolio_sorting.py apps/api/tests/test_portfolio_sorting_postgres.py apps/api/tests/test_portfolio_pagination.py apps/api/tests/test_mobile_postgres.py apps/api/tests/test_auth.py apps/api/tests/test_private_uploads_postgres.py apps/api/tests/test_scanner_queue.py apps/api/tests/test_image_rectification.py apps/api/tests/test_image_service.py apps/api/tests/test_valuation_identity_provenance.py`。最终135通过、3项显式性能测试跳过（性能结果单独记录如下）。警告为现有TestClient弃用提示及两项刻意构造100MP头部的拒绝测试；正常用户图片不采用该超限样本。
- 手机浏览器：`MOBILE_BROWSER_CHECK=1 CARD_PLATFORM_TEST_PORT=55443 bash scripts/test-mobile.sh`，390×844 Chrome两条流程通过：双面→裁切→确认；背面单图→确认→正面补拍→卡片数量保持2→全库排序/私有图片。网络/API/数据库是真实本地服务，外部识别模型边界用明确标注的合成结果。
- Web浏览器：`WEB_CAPTURE_BROWSER=1 CARD_PLATFORM_TEST_PORT=55440 bash scripts/test-mobile.sh apps/api/tests/test_capture_web_browser.py -s`。真实能力查询、背面录卡、评级盒保留整图、人工确认及back-only缩略图通过，外部模型不调用。
- Web最终118单测通过，Next生产构建通过；Mobile最终41单测通过、TypeScript/Vite生产构建及`cap sync ios`通过。手机两条真实浏览器流程在最终后端代码上再次通过。
- Swift四个文件通过语法解析；本机Foundation/CoreGraphics可执行验证10项取景状态/坐标行为，以及临时文件释放、路径隔离和清理；PBX plutil通过。这些不等于iOS SDK类型检查或真机运行。
- 独立审查发现并修复：编辑器未完成时提交旧图、裁切/旋转互相覆盖、旧服务器矩形编辑回归、能力查询路径错误和网络失败误降级、补拍目标删除约束、已确认照片被worker重判向；补拍身份污染风险已通过真实PostgreSQL回归；已人工确认录卡的条目，后续估值不能再以扫描任务的原始建议覆盖数据库人工字段。

### 扩展回归中的既有失败

在上述后端组合中额外加入`test_valuation_worker.py`，结果为224通过、3跳过、1失败：`test_current_cardhobby_revalidation_discards_historical_matcher_conflicts`。测试期待`exact_or_near_exact`，现有卡淘匹配依赖返回`same_player_product_analog`。用Git HEAD中的`revalidated_cardhobby_assessment`和`revalidated_cardhobby_tier`两个入口函数，搭配工作区现有匹配依赖，仍复现同一断言；该测试不执行本轮修改的数据库`_load_identity`。因此没有为使测试变绿而调整用户既有卡淘匹配逻辑，也不将扩展套件报告为全通过。

新增6项GET/HEAD错误响应回归先失败后通过：不存在/格式错误的私有图片地址也返回`private, no-store`及凭证Vary，避免失败响应被共享缓存。现有鉴权与文件响应保持原逻辑。

本轮日志保存在`work/mobile/stage2-api-focused.log`、`stage2-api-expanded.log`、`stage2-web-tests.log`、`stage2-mobile-browser.log`。测试仅使用临时库和合成图片，不包含账号凭证或真实卡库。

### 性能样本和未验证项

本机临时PostgreSQL合成100/1000/5000张，冷请求15.69/68.08/465.41ms；20次热请求p95为3.33/6.40/20.13ms；快照计入253883/2539883/12743883 bytes；测试进程峰值RSS约393/405/466MB。样本以简单人工估值为主，另有真实联合估值计算反转排序回归。没有限制为2核4GB，不能把结果当作云服务器容量承诺。

仍未验证：完整Xcode编译/签名/真机、HEIC真实照片与相机坐标/功耗/内存；至少20组有人工标注的授权卡片/评级盒/反光/缺角样本；30张真实图片传输字节和首屏耗时对照；云端迁移/发布回退；Linux Docker构建（本机无Docker）。不据合成图或模型替身声称真实识别率/裁切准确率提高。

截图：`work/mobile/crop-phone.png`、`sorted-library.png`、`web-capture-smoke.png`。本次临时账号与数据库已随测试服务退出清理。
