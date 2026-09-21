# 2026-09-16 混合尺寸整版误切审计

## 结论

新扫描 `58da166f-c6ce-4dc1-a27f-09a23655883f` 实际有 5 张卡：左侧三张横放，右上是一张含评级标签的 PSA 竖向 slab，右下是一张竖向 one-touch 签字卡。v18 并非识别到 8 个真实物体，而是选择了固定 4×2 整床网格：右侧两张竖卡分别被水平缝切成两半，左下纯背景被补成第八格。

这不是单图特例。v18 历史回放仅剩的四个失败 SHA 组全部属于同类混合布局：会话 `21facd5d…` 的两个独立源都确为 5 张，会话 `f0bf8f4e…` 的源确为 6 张。原 expected count 均经原图复核正确，不应为提高指标而改标签。最终 v19 已把四组全部恢复为真实数量，完整语料 71/71 通过且没有旧通过样本退化。

## 源图与安全状态

- session：`2e149a28-f7f1-4333-850e-1e59d3225b38`
- 状态：`front_scanned`、未提交、`pairing_requires_review=true`
- 数据：仅 front revision 1；无配对 proposal、无 scanner job
- 原图：`data/flatbed-scans/c6e6987e-eec0-40a0-a9de-92ce5d51a7cc/2e149a28-f7f1-4333-850e-1e59d3225b38/58da166f-c6ce-4dc1-a27f-09a23655883f/original.png`
- SHA-256：`9cd8297acc7f54d8eacce1ad0746f053dad577c34987b83a70c7dd3884bd6365`
- 尺寸与 DPI：2480×3507；Image Capture Core 原生 300 DPI，尺寸绑定验证通过

本审计只读查询数据库并运行 standalone/回放；没有改检测器、服务或数据库，也没有重跑已提交的历史会话。

## v18 复现

冻结 baseline detector SHA 为 `af8662f195059a0a0485f26d929a0a20fe1394e863a8aef70f066a169acd3ae7`。调用仅为 `detect_layout(content, dpi=300)`，没有传 expected count 或 prior。

- 输出：8 个框，4×2，score 0.9015
- 八框恰为 `x∈{0,.5}`、`y∈{0,.25,.5,.75}`、`w=.5`、`h=.25` 的固定网格
- `grid_shape_instance_support=3/8`，但 `current_grid_evidence_score=0.9015`
- 右侧四个半卡 crop 均出现 `content_crosses_crop_boundary`
- 左下空格的检测证据为 `ink=.891, edge=.000, luma=181.1`
- standalone 质量评估没有将警告作为语义合并依据；持久化 SAM 流程最终为 8 区、7 usable、1 个 `blank_or_fragment`

原始复现：`/private/tmp/flatbed-mixed-five-baseline-af866-20260916.json`。

## v19 真实流水线候选验证

在不写数据库的 shadow `store_sheet` 中，候选 v19 走完真实 SAM、旋转、裁切与质量链路后得到 5 区，5 区全部 usable，error 为 0。布局仍自行报告 `rows=4, columns=2`，但不再把整床网格当作八张卡；最终保留的是 5 个 `physical_object_envelope`。

回放评分已改为使用每个最终 crop 的 `source_crop_points` 凸多边形，而非宽松的轴对齐 bbox。五个金标打印区与五个 crop 一一对应，覆盖率分别为 1.000000、1.000000、1.000000、1.000000、1.000000；邻卡污染均低于 3%，左下验证空白区最大覆盖为 0。完整 PSA 标签与下方卡面、one-touch 编号和签字区均在裁图内。严格几何失败列表为空。

- shadow 结果：`/private/tmp/card-flatbed-mixed-20260916.k0xhQs/verify.json`
- 结果 SHA-256：`dee3297ecc5454e603e06076f3e7815365e26171f3af8ac4b6ceacd2362bb3da`
- overlay：`/private/tmp/card-flatbed-mixed-20260916.k0xhQs/58da166f-c6ce-4dc1-a27f-09a23655883f-overlay.jpg`
- crops：`/private/tmp/card-flatbed-mixed-20260916.k0xhQs/58da166f-c6ce-4dc1-a27f-09a23655883f-crops.jpg`

该文件是 Otsu 曝光路线加入后的第二轮 shadow：SAM 4 区接受、1 区因物体包络覆盖保护而回退，最终仍是 5 usable / 0 error，且 `object_integrity.minimum_object_coverage=0.95144`。这只证明目标真实源的完整流水线候选已通过；最终冻结版本仍需完整历史 71 组 standalone 回放，不能用单图结果代替无退化证明。

## 人工金标

红色完整物体外包络与绿色必须保留打印区域已在 `/private/tmp/flatbed-mixed-five-gold-proposal.png` 目视复核。manifest 评分使用接近完整的打印区，不使用只覆盖中心约 75% 的弱标签：

| 物体 | 完整 envelope（px xyxy） | 必须覆盖打印区（px xyxy） |
| --- | --- | --- |
| 左上 Galactus 横卡 | `[0,0,1180,890]` | `[50,65,1115,820]` |
| 左中 Edgecombe 横卡 | `[0,875,1190,1815]` | `[80,980,1120,1725]` |
| 左下 Gradey Dick 横卡 | `[0,1805,1300,2695]` | `[80,1880,1150,2605]` |
| 右上 PSA Luka slab | `[1475,0,2480,1705]` | `[1580,90,2400,1560]`，从 PSA 标签主区连续覆盖到卡底 |
| 右下 Cedric Coward one-touch | `[1575,1800,2460,3200]` | `[1600,1970,2400,3100]`，包含完整编号与签字区 |

左下 `[80,2850,1050,3380]` 为经原图确认的纯扫描床；任何检测框覆盖该区域超过 10% 都是虚构框。每个必需打印区要求至少 96% 覆盖、五个区域一对一匹配，邻卡区域覆盖不得超过 3%。这些标签只在检测后评分，绝不作为检测输入。

## 历史同类样本

| session / source | 人工实数 | 真实布局 | v18 输出 | 状态 |
| --- | ---: | --- | ---: | --- |
| `21facd5d…` / `5bf6682a…` | 5 | 左三横 + 右二竖 + 左下空床 | 8 | 历史已提交，不自动重写 |
| `21facd5d…` / `69b56186…`、同 SHA `83b49571…` | 5 | 同上 | 8 | 历史已提交，不自动重写 |
| `f0bf8f4e…` / `bb91de95…` | 6 | 左四横 + 右二混合 + 右下空床 | 8 | 历史已提交，不自动重写 |

对应目视标注图为 `/private/tmp/flatbed-21fac-gold-proposal.png` 与 `/private/tmp/flatbed-f0bf-gold-proposal.png`。源 SHA、尺寸和完整对象区域已加入 manifest；标签未被改成算法输出。

三个历史源都是 back，不能只验证 standalone。只读数据库确认：`5bf6682a…` 与 `69b56186…` 的真实 latest-front prior 是 front revision 2 `7691e09a…`（5 区）；`bb91de95…` 的真实 prior 是 front revision 3 `c07654cc…`（6 区）。最终冻结版本还要以这些 prior 走真实 `store_sheet` + SAM shadow，所有数据库写调用由 mock 接管；不得对已提交会话写新 revision。

## v18 全历史基线

更新后的清单覆盖 108 个 revision 文件，按 SHA-256 + expected count + 实际 DPI 去重为 71 组；67 组通过，4 个失败组正是上述混合布局。

- 数量召回率：0.943662
- 无重复框：1.0
- 无整版误框：1.0
- usable region：0.998134
- 关键几何通过率：0.666667

在多边形评分器升级后重新运行同一 v18 源码，指标与失败集合完全不变。可比基线报告：`/private/tmp/flatbed-mixed-format-baseline-af866-polygon-20260916.json`，SHA-256 `762d644d3dad0ec95d8db6a3f78528a3765a15de864efb67aa772f60f00a1b36`。

## v19 全历史结果

最终冻结 detector SHA 为 `bf00fa91e3fb8f462bc929ff8d1d49b7e374679ab017db8cc52109b62f2e6075`，回放进程启动和结束时源码 SHA 一致。调用仍只有原图像素与数据库记录的 DPI；没有 expected count、gold 或 prior。

- 108 个 revision 文件按 SHA + expected + DPI 去重为 71 组，71/71 通过；
- 数量召回、无重复框、无整版误框、usable region、关键多边形几何通过率均为 1.0；
- v18 原 67 个通过组没有一组退化；
- 四个 mixed-format 失败组分别从 8→5、8→5、8→5、8→6，全部与原图人工实数一致；
- `bb91de95…` 的 unusable 数从 1 降为 0，其余组也没有新增质量失败。

报告：`/private/tmp/flatbed-mixed-format-postfix-bf00fa91-20260916.json`，SHA-256 `05aa0befa1d446f3e0d8eaac61e943024ba811ff96df307bd0d2a89182b3fb26`。

## 历史背面真实 prior + SAM 验证

用只读数据库取出各会话真实 latest-front prior，再调用完整 `store_sheet`、SAM、旋转、裁切及质量链路；服务内所有写调用都由 `AsyncMock` 接管，三个已提交会话没有任何数据库变更。冻结 detector 与 service 在运行前后 SHA 均一致（service `54b9c9f4…`）。

| back source | prior | 输出 | 质量 | prior 处理 | 完整性最小覆盖 |
| --- | --- | ---: | --- | --- | ---: |
| `5bf6682a…` | `7691e09a…` rev2 / 5区 | 5 | 5 usable / 0 error | 当前完整物体权威；跳过旧槽位 | 0.97634 |
| `69b56186…` | `7691e09a…` rev2 / 5区 | 5 | 5 usable / 0 error | 当前完整物体权威；跳过旧槽位 | 0.96453 |
| `bb91de95…` | `c07654cc…` rev3 / 6区 | 6 | 6 usable / 0 error | 当前完整物体权威；跳过旧槽位 | 0.96480 |

三组最终 `source_crop_points` 的严格 gold failures 均为空；16 张实际 crop 已逐张目视，完整卡面和评级标签保留，无邻卡混入，也没有为空床生成 crop。低外轮廓置信、贴原图边缘等 warning 仍保留为可复核提示，没有被伪装成高置信度。

shadow 报告：`/var/folders/dk/k3p_mgq90773p7w5jmcs8n240000gn/T/card-flatbed-mixed-history-20260916.szv6pb3m/verify.json`，SHA-256 `3a2f715ba4811630609d16f8b6b1571c2bb677393424e6538d3c0be62d4e1cd1`。

## 回归契约

新增 `apps/api/tests/test_flatbed_mixed_format_replay.py`，验证：

1. 源 SHA、尺寸和数据库 DPI 被固定；
2. PSA gold 必须同时包含评级标签与完整卡面，签字卡 gold 必须包含完整签字区；
3. 固定 4×2 网格同时触发 `required_content_region_fragmented` 与 `verified_blank_region_covered`；
4. 五个完整混合尺寸 envelope 能通过；追加左下虚构框后必须失败；
5. 实际 crop polygon 驱动打印区覆盖、邻卡污染与空白区评分；相同 bbox 的内缩菱形不能假通过；
6. expected count、gold 和 prior 均不进入 detector。

新测试覆盖 polygon、输出报告的 NumPy 类型归一化与原有 topology/replay 契约。最终检测器已完成 71 组严格回放及三个带真实 prior 的历史背面全链路 shadow，全部满足上述验收条件。
