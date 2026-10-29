# 单卡拍卖草稿 Implementation Plan

> 使用 Superpowers 测试先行、独立组件并行执行与最终审查；Spec Kit 共用本目录 spec.md / plan.md / tasks.md。

**Goal:** 单卡选图/电脑触发 Redmi 拍照后生成并保存可编辑的拍卖草稿，核实卡淘草稿能力且绝不发布。

**Architecture:** PostgreSQL 独立草稿记录 + FastAPI 所有权检查和私有媒体，独立 React 编辑器接入网页与手机。电脑 localhost:8767 手机助手使用 ADB 触发快门，只回传本次新照片。模板生成文案，订单读取为非强依赖。

**Tech Stack:** 现有 Python/FastAPI/psycopg，Next.js/React/TypeScript，移动端 React/Capacitor，Node 本机助手。

**Spec:** specs/031-auction-drafts/spec.md

## Global Constraints

- 原有未提交修改全部保留，仅增量修改所需接入点；不提交或覆盖其他任务。
- 所有数据库验证使用独立测试库，不回写真实卡库和知识库。
- 拍卖状态仅为 draft；远端能力未知时 disabled，不调用猜测的卡淘写入接口。
- 本任务直接在现有非主干功能分支增量实施，避免新 worktree 遗漏大量尚未提交的依赖成果。用户后续明确授权生产发布；从已部署版本叠加本功能，独立冻结源文件，保留其他未提交修改。生产迁移先备份，切换仅替换镜像，保留配置、挂载与持续开启的成本worker。

## Review Focus

- 跨账户、删除卡片后的文本与图片访问必须拒绝。
- 两个窗口同时保存时旧 revision 不可覆盖新版本。
- 订单接口失败不丢照片与用户文本；标题不得复制他人承诺。
- 手机只能指定已枚举设备，快门后找不到新图必须失败，不回传旧照片。
- 界面必须诚实显示平台草稿与卡淘能力状态，绝不混淆成功状态。

## Interfaces

所有 HTTP 请求沿用现有 cookie/Bearer 认证。以下响应均为 AuctionDraft：

- POST `/v1/auction/items/{item_id}/draft` 无请求体，幂等创建/恢复。
- GET `/v1/auction/drafts/{id}`。
- PATCH `/v1/auction/drafts/{id}` JSON `{revision,title,description,condition_notes,selected_photo_ids}`，返回完整草稿。
- POST `/v1/auction/drafts/{id}/images` multipart `revision,image,side(front|back|detail),source(upload|camera|redmi),request_id(UUID)`，同一重试标识和内容幂等追加且选中新图，返回完整草稿。已发生其他修改时先拒绝旧版本，刷新核对后可恢复重试。
- POST `/v1/auction/drafts/{id}/generate` JSON `{revision,selected_photo_ids,condition_notes,use_order_examples:true,automatic:false,edited_title?,edited_description?}`，返回完整草稿。新照片automatic=true时保留已保存人工文案；未保存人工文字通过edited_title/edited_description一并持久化，显式重新生成才替换。
- GET `/v1/auction/drafts/{id}/images/{photo_id}` 私有照片。
- POST `/v1/auction/drafts/{id}/cardhobby` 显式返回409草稿接口未核实，不执行外部写入。

AuctionDraft = `{id,item_id,revision,status:'draft',title,description,condition_notes,selected_photo_ids:string[],photos:AuctionPhoto[],identity:Record<string,unknown>,generation_basis:Record<string,unknown>,external:{supported:false,status:'unavailable',reason:string},updated_at:string}`。

AuctionPhoto = `{id,side:'front'|'back'|'detail',source:'existing'|'upload'|'camera'|'redmi',url,sha256}`。

本机助手（仅网页调用，不向手机端暴露本机回环入口）：GET `http://127.0.0.1:8767/v1/devices` header `X-Camera-Client: cardsp-web-v1` -> `{devices:[{serial,name}],token}`；POST `/v1/capture` header同上及 `X-Camera-Token`，JSON `{serial}` -> image/jpeg 或 image/png。来源白名单与 Canon 助手一致。

## Task 1: 持久化、私有图片与文案生成

Files: `db/migrations/0091_auction_drafts.sql`, `apps/api/src/card_platform/auction_{drafts,api}.py`, `main.py` router接入；`tests/test_auction_drafts.py`, `test_auction_drafts_postgres.py`。

- 先写失败测试：未知属性不生成卖点、人工品相被保留、照片至少1/最多12、版本与照片归属校验；运行并确认失败。
- 建立唯一 owner/item 草稿，使用 advisory lock 幂等创建和 revision compare-and-swap 更新；原图独立快照，新图独立存储。
- 模板生成标题和内容；仅订单样例查询失败回退并明确标记，不吞数据库异常。
- 验证合成图片API、测试库迁移、跨账户/删除卡片/并发冲突/重复创建；原卡字段和图片引用不变化。

## Task 2: 电脑手机助手

Files: `scripts/android-camera-helper.mjs`, `scripts/tests/android-camera-helper.test.mjs`, `docs/android-auction-camera.md`。

- 先写 HTTP 来源/令牌/设备选择/旧图拒绝失败测试，运行确认失败。
- ADB设备枚举，指定串号启动相机，发送一次快门，轮询新建且稳定照片并回传，限制大小与格式，断连与超时可恢复；不删除手机照片。
- 执行node测试，真实枚举手机；无设备明确记录，不把mock当实机通过。

## Task 3: 网页与手机单卡草稿编辑器

Files: shared `packages/card-core/src/auction.ts`，网页 `components/auction-draft-panel.tsx`, `lib/local-camera.ts`, `app/page.tsx`接入；手机 `src/AuctionDraft.tsx`,`LibraryPage.tsx`接入。

- 先写交互失败测试：打开恢复、选图生成、新拍/上传自动生成、编辑保存、冲突提示、外部草稿不可用不可发布。
- 使用以上统一HTTP接口；桌面增加助手连接、设备选择、快门；手机使用本机拍照/文件输入。只在网页端展示 USB 设备控制。
- 网络失败保留用户输入及已拍文件，防止重复并发请求；图片用现有私有图片鉴权机制。
- 冲突刷新进行三方合并：未修改字段读取服务器最新值，本页真正修改字段保留并展示冲突服务端值，不用新revision盲目覆盖其他字段。
- 网页typecheck、手机build及针对性组件测试；可用时浏览器走实际用户路径。

## Task 4: 核验、审查与验收记录

- 只读调研卡淘官方入口与现有订单接口，记录实际草稿能力和未解决障碍。
- 独立审查后修复重要问题，运行相关测试、构建，回看 spec 每项需求。
- 在 `verification.md` 记录命令、结果、实机和卡淘限制；不假称部署完成。

## Task 5: 授权上线（用户后续要求）

- 冻结网页/API0.6.7与手机1.1.19（23），执行相关回归和构建；只提交本次功能，其他未提交工作保留。
- 云端备份数据库并校验归档，事务执行0091新增草稿表迁移。基于现有API/Web镜像叠加本次模块，保留现有Compose各层及worker/视觉/独立搜索配置；失败只回退镜像。
- 使用原个人开发签名覆盖安装iPhone版，回读版本、启动与延迟进程；不得卸载或清除数据。锁屏需要用户解锁。
- 本机手机助手用LaunchAgent持续运行，验证只监听回环、授权设备枚举和HTTP；公网匿名鉴权及已登录浏览器验证单卡入口、图片和草稿恢复。卡淘远端草稿仍禁用，变焦/预览/对焦未实现不纳入上线声明。
