# 云端 Canon 扫描实施计划

**Goal**: 接通 cardsp.cn → Mac Canon 原图 → 现有云端扫描批次。
**Architecture**: 回环 Node HTTP 助手复用 Swift ImageCaptureCore CLI；网页内存连接令牌和待上传图；上传接口接收可选分辨率元数据。
**Tech Stack**: Node 内置 HTTP/child_process/fs，现有 React/TypeScript、FastAPI/Python、Swift。
**Spec**: [spec.md](spec.md)

## Global Constraints / Constitution Check

保留已有未提交修改和线上视觉迁移；云账户权限、来源白名单、私有图片与身份门禁不变；不引入依赖或数据库迁移。授权范围由用户确认的云端扫描及 AGENTS 持续推进规则确定，本会话直接执行同一份任务。

## Review Focus

其他 Origin/Host 或缺令牌不得探测/取图；扫描忙与退出释放；云上传失败后不重复扫描；预览元数据不能冒充图像尺寸；发布基线不得覆盖线上模型适配。

## Task 1 — 本机助手

**Files**: `scripts/canon-scanner-helper.mjs`、`scripts/launchd/com.cardplatform.canon-scanner.plist`、`scripts/tests/canon-scanner-helper.test.mjs`。

接口：GET `/v1/device` 返回 `{name,serial,capabilities,token}`；POST `/v1/scan` 接受 `{dpi:300|600,preview:boolean}` 和 `X-Scanner-Token`，返回 PNG 及 `X-Scan-Resolution`。来源、Host 检查先于路由；OPTIONS 支持私有网络预检。进程私有临时目录/固定新路径；校验完成事件路径在该目录；结束/断开/超时 SIGINT，5秒后强制释放。测试使用真实 HTTP 与临时可执行桥接，不访问实物设备。

- [ ] 先写并观察失败的 HTTP/扫描互斥、来源、令牌、超时释放测试。
- [ ] 实现助手与 launchd；运行 `node --test scripts/tests/canon-scanner-helper.test.mjs`。

## Task 2 — 云端图像元数据

**Files**: `apps/api/src/card_platform/flatbed_api.py`、`apps/api/tests/test_flatbed_cloud_upload.py`。

现有 multipart upload 增加可选字符串 form 字段 `source_resolution`，限 4096 字符且解析为对象，传 `store_sheet(...,source_resolution=...)`；像素与 DPI 仍由现有解析器按真实解码图像核验。旧文件上传不提供字段保持原逻辑。测试 malformed/非对象、无元数据、有效概览及 owner/session 参数。

- [ ] 写失败测试，运行后实现，再跑相关 flatbed API 测试。

## Task 3 — 网页连接和上传恢复

**Files**: `apps/web/lib/local-scanner.ts`、`apps/web/components/flatbed-batch-panel.tsx`、`apps/web/lib/api.ts`、网页相关测试。

云端域名使用 `http://127.0.0.1:8766` 助手，所有本机请求 credentials omit；本机网页保留原生 API capture。用户点击连接触发权限探测，禁止将助手 token 带到云 API。助手原图经扩展的 `uploadFlatbedSheet` 提交；保留真实 metadata。取图与上传状态分开，上传失败显示重试及下载并锁住新扫描/批次；不丢弃已取图。原有云端配对/提交不变。

- [ ] 测试云路径无 native capture、localhost credentials omit、真实 form bytes、metadata、失败重试无二次 scan；观察失败后实现。
- [ ] 跑全部网页单测、TypeScript、相关 API 测试并构建线上基线候选。

## Task 4 — 安装、发布、验证

**Files**: `docs/canon-lide400-batch-scanning.md`、本目录 `verification.md`，私有执行回执留 `.runtime/`。

安装本机 launchd；候选仅基于当前在线源码叠加相关补丁，构建并验证后切换 Web/API；不发布无变化手机二进制。读取运行 settings 验证 worker=true、deepseek-flash/视觉 thinking disabled/搜索隔离；线上浏览器连接设备并真实取图上传，保留原图哈希及云会话响应；不确认入库。最后自审范围与任务清单。

- [ ] 完成安装、候选构建、健康验证与网页真实路径；记录未覆盖项。
