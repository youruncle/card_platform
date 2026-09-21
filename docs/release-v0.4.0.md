# Card Platform v0.4.0

发布日期：2026-09-08

## 主要更新

- 重构卡淘成交召回与产品身份判定：以 `MATCH`、`UNKNOWN`、
  `EXPLICIT_CONFLICT` 三态区分可用弱证据和明确错版，避免因标题省略品牌或产品而误杀成交。
- 统一可比成交分级、稀疏样本区间估值、低置信度展示、失败重试、旧证据失效和审计回填；
  保留明确异球员、异赛季、异平行、异卡种、异限编、异签字/物料及 lot 的硬冲突保护。
- 新增产品身份共享层、卡淘跨品牌回归集、估值失败审计与安全重跑工具，以及经人工核验的
  CardHobby 估值真值样本。
- 扩展 LiDE 400 扫描流程：加入方向元数据、裁切质量检查、槽位细化、并发识别和可选 SAM2
  分割，同时保留经典检测回退路径。
- 资产列表改为服务端分页，并补充识别置信度和待核对状态展示。
- 图片查看器支持点击空白关闭；操作栏顶部居中并在窄屏换行；横图、竖图按剩余视口自适应。
- Next.js standalone 构建自动同步静态资源，避免重启后新页面引用的 chunk 返回 404。

## 升级说明

1. 应用数据库迁移 `db/migrations/0067_scanner_orientation_methods.sql`。
2. 按需配置 `VALUATION_CARDHOBBY_ONLY`、`SCANNER_WORKER_CONCURRENCY` 和 `FLATBED_ML_*`。
   SAM2 默认关闭，不影响未安装模型的环境。
3. 重新构建 API 与 Web；Web 的 `postbuild` 会把静态资源复制进 standalone 输出。
4. 本地实时审计输出包含库存 UUID，不属于发布资产；需要时运行
   `scripts/audit_unvalued_cards.py` 在目标数据库重新生成。

## 发布验证

- API：在全新临时 PostgreSQL 上应用全部迁移后，976 项全部通过。
- Python 静态检查：Ruff 通过。
- Web：TypeScript 类型检查和 Next.js production build 通过。
- 图片查看器：桌面端居中、窄屏换行、图片适配、空白关闭和图片防误关均通过实际浏览器验证。
- Flatbed：57 份 SHA-256 去重真实语料强制回放无回归；独立计数召回率 80.70%、
  无重复框率 100%、整版误检拒绝率 100%、可用裁图率 99.28%、关键几何通过率 100%。

估值修复的逐类结果和仍需人工确认的身份问题见
`docs/valuation-remediation-summary.md`。
