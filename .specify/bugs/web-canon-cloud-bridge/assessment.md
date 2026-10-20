# 网页 Canon 扫描返回 500（2026-10-20）

## 原始症状与证据

- 用户截图：`/Users/song/Desktop/截屏2026-10-20 23.23.20.png`。在 `https://cardsp.cn` 的 Canon 批量录入页，设备状态及扫描请求显示“API 服务不可用，请确认后端 8000 端口已启动”。
- 线上 API 容器 `card-platform-api:0.6.3-20261020-deepseekweb1` 实际 healthy；日志中 `/health/ready` 与 `/v1/scanner/jobs` 继续成功。
- 线上 `GET /v1/flatbed/device`、批次 `c7623dc2` 的 `capture/front` 请求在 `FlatbedService._run_bridge` 的 `asyncio.create_subprocess_exec` 抛出 `FileNotFoundError`，导致未处理的 HTTP 500。
- 本机执行 `./scripts/canon_lide400_bridge.swift capabilities --timeout 15` 成功返回 Canon flatbed 的 DPI、色彩、扫描范围等能力；未执行真实整版扫描或新增卡库记录。

## 根因

网页的 Canon 设备探测和 capture 请求走云 API，但云 Linux 容器无法启动依赖 macOS ImageCaptureCore 的本机扫描程序，也不能直接控制接在用户 Mac 上的 USB 设备。桥接启动的 `OSError` 未被转成 `FlatbedBridgeError`；网页又把无结构化 detail 的 500 统一解释成 API 未启动，并且探测失败后仍允许继续点击扫描。

现有实现只支持 API 与扫描仪同机的直接扫描；云端整版图片导入是独立入口。代码库目前没有 cardsp.cn 到本机 Canon 的扫描桥接链路。

## 最小修复（工作树，未发布）

- `flatbed_service.py`：只在启动扫描子进程时捕获 `OSError`，转为明确的扫描不可用说明，提示连接扫描仪的 Mac 本机网页或整版图片导入。现有 device/capture 路由返回 503，capture 进度变为 failed；取消和超时的设备释放逻辑保留。
- `flatbed-batch-panel.tsx`：设备探测成功后才允许本机扫描；失败及探测期间禁用概览、正式扫描，并在 scan 入口防守。整版图片导入、会话恢复与已有卡片处理入口保留。
- 仅添加上述修复相关代码和测试，保留这些文件已有未提交的正背配对与会话版本改动。

## 验证

- 修复前新增回归：缺少桥接、缺少解释器、不可执行共 3 种启动错误，device/capture 共 6 例，全部因原始 FileNotFoundError/PermissionError 失败；网页不可用扫描按钮回归失败，原有 3 例通过。
- 修复后 API：`PYTHONPATH=apps/api/src .venv/bin/python -m pytest apps/api/tests/test_flatbed_unavailable.py apps/api/tests/test_flatbed_service.py apps/api/tests/test_flatbed_api.py apps/api/tests/test_flatbed_capture_resolution.py -q --tb=short`，34 passed。
- 网页相关：`node --import tsx --test tests/flatbed-progress.test.cjs tests/pair-ingestion.test.cjs`，6 passed。
- 网页全部单测：在 apps/web 执行 `node --import tsx --test tests/*.test.cjs`，158 passed。
- `pnpm typecheck` 与本次文件 `git diff --check` 通过。

## 尚未完成的用户路径

本次工作树修复消除了未处理异常和误导性入口，尚未发布，也没有实现云网页驱动本机 USB 扫描仪。已向用户确认希望继续在 cardsp.cn 扫描并保存到云端，还是通过本机网页扫描并保存在本机；选择会决定后续桥接需求。未进行生产数据库写入、生产重启或手机发布；生产 deepseek-flash、独立搜索配置和自动成本 worker 均未改动。未运行 API 全量测试或真实扫描、裁切、识别、入库端到端回归。
