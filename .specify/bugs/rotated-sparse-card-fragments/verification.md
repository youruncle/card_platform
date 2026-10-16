# 验证记录（2026-09-22）

## 目标症状与实际裁切

- 原图：4 个网格碎片 → 2 个完整旋转多边形；未向检测器传预期数量或手标几何。
- 手工标注两张卡面四角，自动测试验证每张至少 97% 覆盖且各有唯一裁图。
- 实际 `FlatbedService.store_sheet` 在隔离输出目录写出两张完整 JPEG，已视觉检查。
- service 输出：usable_count=2、needs_review_count=0、error_count=0；整卡完整性 reference_count=2、minimum_object_coverage=1.0、无错误。
- 此服务回放仅模拟数据库连接和朝向分类返回，禁用 SAM2；真实检测、质量评估、透视变换和 JPEG 编码均实际执行。没有写入用户数据库。OCR 朝向、身份字段、SAM2 外部模型与实际网页路径未验证。
- 输出及原始结果：`work/rotated-sparse-fix/`；预览 `verified-polygons.jpg`、结果 `service-output.json`。

## 自动测试

- TDD 首轮：2 failed / 2 passed，失败分别为原图四块及合成图六块。
- 审查防回归用例：先观察 2≠3 失败，收紧路由后通过。
- 最终专项：8 passed（角度、原图、浅色/紧贴/不规则物体、混合布局）。
- 最终 API 全量测试：2101 passed、67 skipped、1 warning（Starlette/httpx 弃用提示），无失败。
- Ruff 与 git diff --check 通过。

## 历史真实扫描回放

- 最终代码运行 `scripts/replay_flatbed_layouts.py --enforce`，退出码 0。
- 112 个扫描文件，按 SHA/DPI/预期数量去重后 73 个独立用例，73/73 通过，regressions=[]。
- 本次样本包括在内；数量、重复框、整页误识别、裁图可用性与关键手标几何检查均通过。这是固定样本集结果，不是整体识别准确率。
- 报告：`work/rotated-sparse-fix/replay-final.json`；API 完整输出：`work/rotated-sparse-fix/api-tests-final.log`。

## 边界

- 原始样本保留本地；缺失时真实图片测试显式 skip，合成用例仍运行。
- 本次没有重写旧扫描的保存结果。新版本加载后，已有 `/v1/flatbed/sheets/{sheet_id}/reprocess` 路由可基于原图生成新修订，无需重扫。
- 紧贴成组后整体倾斜的卡片仍可能合并（旧版与新版均能复现），强反光、遮挡、低对比度不在本次通过样本结论之外作保证。

## 本机部署（2026-09-22 01:02 +08:00）

用户授权重新部署后，确认 launchd 的 API 直接加载当前工作区源码，且相关任务表无 queued/running/processing/capturing 任务。执行 `scripts/local-stack.sh restart-api`，API PID 从 73717 更新为 33706。

部署版本：`physical-object-layout-v21`；检测器 SHA-256：`b12679a1437ae6b4046e8633a3dca02b868c0aaaf86513d75cfb37c3d0777ae9`。
`/health`、`/health/ready`、Web 首页均 HTTP 200；未登录扫描会话接口保持 401。相同部署源码回放目标原图输出 2 张。部署证据：`work/rotated-sparse-fix/deployment.json`。

此次为 API 代码生效重启，前端代码无改动；未生成旧扫描的新修订，未更改已有卡库数据。
