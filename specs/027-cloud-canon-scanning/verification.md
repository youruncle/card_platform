# 云端 Canon 扫描验证与发布

2026-10-20 已发布网页/API `0.6.3-20261020-canon1`。源码提交 `dc13501c` 与 `118892de567ef9eafdd1c65b7a3899a755a758fb`，基于实际线上提交 `33a84b4e`；原工作区已有修改保留。

## 实际用户路径

在用户已登录的 Chrome `https://cardsp.cn` 中刷新，恢复原批次 `c7623dc2`，点击“连接本机扫描仪”显示 Canon LiDE 400 已连接，再点击正面概览。

- 本机真实取图 → 网页当前登录会话上传 → 云端原批次显示 **8 张正面、版本 1**，8 个区域缩略图可用。
- 云 API 日志为一次 `/sheets/front` HTTP 201；新容器期间没有云端原生 `/capture/` 调用。
- 云原图 SHA-256：`80bdc883dd28bececeedf0d10103956fd5242fd2d36e0592ae140963d3958c29`。
- 云记录为 1700×2338、`capture_kind=preview`、`actual_dpi=effective_dpi=200`、会话 `requested_dpi=300`，状态 `verified_native_dimensions`，没有把概览错当 300 DPI。
- 页面显示贴边/布局核对提示；数量 8 与实际扫描床上 8 张卡一致。这只验证本次样本的取图、上传及区域数，不代表身份字段或所有裁图准确率。
- 只读复核卡库仍为 877 张，批次尚未提交入库，只有 1 张整版正面。浏览器保留在可翻面继续扫描的状态。

本机助手另一次直接 HTTP 概览也成功：5,954,076 bytes，1700×2338、200 DPI；私有原图和元数据留在 `.runtime/canon-cloud-20261020/`。它与之后网页扫描是两次不同的真实采集，不将二者哈希混为同一文件。

## 自动与构建验证

| 范围 | 结果 |
| --- | --- |
| 相关 API：cloud_upload、unavailable、service、api、capture_resolution | 40 passed |
| 全部网页单测 `node --import tsx --test tests/*.test.cjs` | 163 passed |
| 助手真实 HTTP/临时子进程测试 | 7 passed |
| TypeScript `pnpm typecheck`、本任务 `git diff --check` | 通过 |
| Next.js 15.5.20 生产构建（当前线上源码基线） | 通过 |
| 候选 Linux 容器：首页、价格页、API 代理、13 项静态资源（含延迟加载扫描 chunk） | 通过 |
| 运行 API 189 个 Python 源清单、候选网页 101 个编译产物清单 | 逐 SHA 验证通过 |

新增用例先观察原始失败再修复：桥接缺失/解释器缺失/不可执行、设备不可用按钮、云取图上传与失败原图重传、窗口关闭取消扫描及探测、600 DPI 会话恢复、助手退出取消扫描。真实 HTTP 还覆盖不可信 Origin/Host、错误令牌、PNA 预检、非法参数、忙碌及超时释放。

一次独立只读审查报告的两个 Important（手动 Node 停止时子进程释放、连接窗口关闭时取消 capabilities 探测）均经失败→通过回归修复；无未处理的 Critical/Important。launchd 本身默认会清理同进程组，不将手动 Node 的问题错误描述为 launchd 默认泄漏。

## 发布方式与生产保护

API 只在实际在线镜像上覆盖 `flatbed_api.py`、`flatbed_service.py`，其余 187 个 Python 源按 SHA 保留。网页基于同一线上提交构建，保留原 Linux 运行时及同版本 Next.js 依赖，不上传本机 `node_modules` 或 Darwin 原生二进制。

首次远端依赖下载因 npm 连接重试停滞，停止的仅是本次候选构建；随后使用同锁文件、同版本依赖完成本地生产编译，编译产物在最终 Linux 镜像中逐 SHA 验证并实测 HTTP 后才切换。最初候选检查只找首页初始 JS，未包含按需加载的扫描 chunk；检查已改为同时验证该独立 chunk，最终 13 项资产均成功。

- API 于北京时间 23:54:28 启动，网页于 23:54:51 启动；API readiness 正常。
- 实际运行 API 环境 `PURCHASE_COST_WORKER_ENABLED=true` 且 `get_settings().purchase_cost_worker_enabled is True`。
- 实际视觉仍是 `deepseek-flash`，`vision_thinking_enabled=False`；模型适配源码逐 SHA 未变。
- 远端 `.env.cloud` 发布前后 SHA 相同；未改模型或搜索的端点/密钥。生产 Compose 只改 API/Web 镜像行。
- API 镜像 `sha256:f2d07a15fdfaf4abde90f86b3bf5e1f3a96c23de017e33231368868bbed355bc`；网页镜像 `sha256:51a64454a286f1398b1f2aa9b9d8cbb9c52a297c440e7e06c23e0a3f4c97e5fa`。
- 本机 LaunchAgent `com.cardplatform.canon-scanner` 已安装并运行，仅监听 `127.0.0.1:8766`，重启后真实设备探测成功。

发布回执、源清单、构建/测试/扫描请求日志留本机 `.runtime/canon-cloud-20261020/` 和远端 `/home/ubuntu/card-platform/releases/0.6.3-20261020-canon1/`；原镜像与 Compose 镜像行备份保留。失败回退逻辑只恢复镜像行，保留实际 worker=true 与环境配置；未在生产演练回滚或恢复数据库。

## 明确未验证

未物理翻转这批卡片，因此未实测本批背面扫描、正背配对及确认入库；未实测真实 300/600 DPI 正式取图或执行新模型识别。未运行 API 全量测试、手机重新构建/覆盖安装；本任务仅改网页 Canon 路径及兼容的云端上传字段，手机无源码变化。本机新样本没有写入本机卡库。
