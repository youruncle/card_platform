# Bug Assessment: 库存正反面错配与双正面录入

- **Slug**: inventory-face-mismatch（调查时自动命名）
- **Created**: 2026-10-19（Asia/Shanghai）
- **Source**: 用户报告；线上库存只读快照、本机原始扫描会话和保存的识别结果
- **Verdict**: valid
- **Severity**: high — 错配进入真实库存，身份、成本候选与估值可能引用不同实卡。
- **Scope**: 本次仅调查、留存证据，未修改应用代码、库存、身份、交易、图片或 worker 配置。

## Report

> 我发现好几张都是正反面配错了，你先看看有哪些，再调查下问题出在哪里，我怀疑是录入的时候，正反面就搞错了。

## Symptom and confirmed inventory

对线上当前账户 877 张未删除库存的 1,754 张图片进行缩略图初筛，疑似样本查看原图；结合本机保留的 867 条扫描任务和源扫描版面追溯。确认以下 16 张异卡错配，以及 4 张双正面、缺少真实背面的记录。

| ID | 正面实际内容 | 当前背面实际内容 | 分类 |
|---|---|---|---|
| 657 | LeBron James，Panini Instant 40,000 分纪念 | LeBron James，Prizm #5 | 同批不同产品错配 |
| 660 | LeBron James，Select | LeBron James，Instant #391 | 同批不同产品错配 |
| 661 | LeBron James，Instant 40,000 分纪念 | LeBron James，Optic Express Lane #2 | 同批不同产品错配 |
| 662 | LeBron James，Prizm Deep Space | LeBron James，Instant #391 | 同批不同产品错配 |
| 663 | LeBron James，Optic Express Lane | LeBron James，Instant #391 | 同批不同产品错配 |
| 664 | LeBron James，Instant 40,000 分纪念 | LeBron James，Select Concourse #54 | 同批不同产品错配 |
| 806 | Victor Wembanyama，Topps Now，41 分/24 篮板纪念 | Stephen Curry，Status #83 | 同批两张位置交换 |
| 809 | Stephen Curry，Status，实卡限编 109/149 | Victor Wembanyama，Topps Now #315 | 同批两张位置交换 |
| 846 | LeBron James，Bowman Chrome HOBBY STARS | LeBron James，Status Trophy Club #5 | 跨批次整版混用 |
| 847 | Shaquille O'Neal，Topps Chrome | Kyrie Irving，Donruss Pass the Rock #3 | 跨批次整版混用 |
| 848 | Kon Knueppel，Generation Now | Hugo Gonzalez，Hoops Block by Block | 跨批次整版混用 |
| 849 | Kobe Bryant，Collectors Edge Impulse 98 | Josh Hart，Mosaic #154 | 跨批次整版混用 |
| 850 | Yang Hansen，Generation Now | Steve Nash，Panini #41 | 跨批次整版混用 |
| 851 | Walter Clayton Jr.，Origins | Josh Hart，Mosaic #154 | 跨批次整版混用 |
| 852 | Tyrese Maxey，Flux | Stephen Curry，Court Kings #29 | 跨批次整版混用 |
| 853 | Allen Iverson，SP Authentic | Giannis Antetokounmpo，Topps Now | 跨批次整版混用 |

| ID | 当前双面实际内容 | 原图追溯 |
|---|---|---|
| 306 | Kobe Bryant，绿色 Prizm 正面 ×2 | 背面源版面的左列仍为正面 |
| 308 | Vince Carter，绿色 Prizm 正面 ×2 | 同上 |
| 311 | Allen Iverson，绿色 Prizm 正面 ×2 | 同上 |
| 312 | Michael Jordan，正面 ×2 | 同上 |

722、765 的上传背面裁切不完整，继续查看完整源版面后未发现异卡矛盾：722 的背面是 Cedric Coward / Memphis / Prizm No.5；765 的源背面 No.5 Road to Finals 具有同一 Kyrie 抛投照片与对应比赛描述。两张另列为裁切问题，不计入确定错配。340 的两面都有相同的 Generations 设计与 G11 标识，不能仅因两面人物不同就列为错配。早期 4、5 存在取景问题，但可见身份相符。

初筛不是逐张实卡核验：相同球员、相同产品且无可辨识差异的两份实卡，不能凭缩略图证明配对无误。未因没有发现矛盾就将剩余库存标记为人工确认。

## Reproduction and integrity checks

1. 冻结线上 877 条当前库存及图片 URL，逐图计算 SHA256，与本机用于检查的图片比较。
2. **1,754/1,754 图片哈希一致**；885 的两张本机缺失图片从线上只读取得并查看。
3. 20 条确认异常记录的 **40/40 当前图片与原扫描任务输入逐字节一致**。错误在原始录入任务里已经存在，并非后续页面展示或本次云端同步换图。
4. 使用历史保存的区域边界与信号，离线运行当前 `propose_pairs`：657、809、846 所在的三批各 8 对，**24/24 配对与历史选择相同**，分别复现 `mirror_y`、`identity`、`mirror_x`。

复现命令（只读，不调用外部模型）：

```sh
PYTHONPATH=apps/api/src .venv/bin/python .specify/bugs/inventory-face-mismatch/replay-pairing.py
```

脚本读取私有 `.runtime/inventory-face-mismatch/provenance.json`。这是错误复现断言，不能当成修复已通过的测试。

## Root cause evidence

### 1. 846–853：未提交会话沿用旧正面，配上后来另一批的背面

同一扫描会话中，源图片的北京时间顺序为：

| 时间（2026-10-14） | 保存的版面 |
|---|---|
| 00:27:13 | 正面 revision 1，包含 HOBBY STARS LeBron 等 8 张 |
| 00:33:49 | 背面 revision 1，与上述 8 张逐张对应 |
| 00:36:21 | 背面 revision 2，已经换成另一批 8 张，检测仅返回 4 个区域 |
| 00:46:16 | 背面 revision 3，另一批，检测 8 个区域 |
| 00:49:30 | 背面 revision 4，另一批，检测 8 个区域 |
| 00:50:10 | 提交旧正面 revision 1 与最新背面 revision 4 |

`flatbed_service.py:810` 按同一会话中每一面的最大 revision 取图，没有固定一组正反版面为不可混用的采集批次。对原始整版图片的观察直接证明这不是翻转方向或同批排列问题：两个版面包含完全不同的八张卡。正确的背面 revision 1 仍保留，可作为后续恢复依据。

界面恢复未提交会话并继续对同一 `session.id` 扫描；更换一面会立即与另一面现存内容重新配对。背面处理期间甚至提示“可以更换下一批卡片”。这些行为允许把后续批次的背面加入旧会话。保存证据能确定发生了跨批次混用；没有足够的点击日志确定是哪一次页面操作或会话恢复触发，不能归咎于用户手动调错配对。

### 2. 657、660–664：年份误作强身份，选错整版翻转

原始正反版面同位置对应正确，算法却选择 `mirror_y`。离线打分：正确 `identity` 合计 5.944642，错误 `mirror_y` 合计 6.551276。

`flatbed_service.py:281` 的 OCR 信号从文本中取第一个四位年份；Instant 正面可见纪念日期 2024，背面产品行是 2023–24，抽取为 2023。`flatbed_matching.py:49` 只要有可比较的结构化字段，就直接返回相等比例，不再利用剩余文字。于是正确产品的两面因年份表达不同得 0 分，不同产品却可能因同为 2024 得 1 分。产品和 insert 没有作为独立强约束。

同一文字分数还会在 `recognition` 默认项重复使用，合计占 28% 权重，足以压过更正确的空间关系。相同球员并不能保证同产品，结果六张错配，其中部分还得到较高置信度。

### 3. 806、809：扫描两面时局部换位，缺少身份信号仍按位置配对

源版面同一批中，库里与文班亚马前两行的位置发生交换。正面两次 revision 的源像素 SHA256 完全相同。八张正面区域保存了 `ocr_failure=local_vision_failed`；没有可比文字时 `_text_similarity` 返回中性 0.5，`recognition` 也复用 0.5，算法按原位置将两张配反。正确对应应为正面 ordinal 0 → 背面 ordinal 2、正面 ordinal 2 → 背面 ordinal 0。

信号只保存了泛化 OCR 失败原因，现有证据不足以进一步断定当时 Apple Vision 失败的底层原因。

### 4. 306、308、311、312：背面扫描原图就有半列没有翻面

2026-09-06 14:57:00 的正面与 14:58:07 的背面源版面是不同照片：右列四张翻面正确，左列 Vince Carter、Michael Jordan、Kobe Bryant、Allen Iverson 在两幅源版面中都朝上显示正面。因此并非导入器把同一个文件重复保存。该会话正反各只有一版，没有保存这四张的真实背面。

### 5. 识别与入库没有阻止以上错误

`image_service.py:873` 的双面识别提示直接假设“同一张收藏卡”，并要求“冲突字段写入 caveats，不要把两张图当成两张卡”。846 的历史结果确实把 Chrome/Panini 冲突解释成设计或 OCR 问题；847–852 多条历史结果明确写出了不同球员、不同产品，依然进入库存。

`scanner_queue.py:613` 自动创建库存前没有可靠的“正反面属于同一实卡”判定。`flatbed_api.py:537` 将质量与置信度视为提示；通用确认即可录入。`flatbed_api.py:480` 还把所有确认都写作 `manual_override=true`，因此该字段不能证明用户手动改变了配对。离线复现已经表明无需人工更改配对，算法就能生成这些错误结果。

## Proposed remediation

**Preferred**：后续修复应同时覆盖采集边界与身份校验，保持一份缺陷记录；不能只放宽成本匹配或只替换显示图片。

1. 正反源版面绑定为明确的一次采集批次；已有双面后补扫、换批或恢复旧会话时显式选择覆盖范围。提交固定 source sheet/revision，避免自动取两面的各自最新版本。
2. 配对时区分赛季、纪念日期、版权与统计年份。单个年份不能覆盖球员、产品、insert 与稳定位置证据；缺失 OCR 保持未知，不应视为身份核验通过。避免对同一文字信号重复加权。
3. 对候选逐面独立读取可见身份，再校验是否属于同一实卡；明确的球员/产品冲突及双正面须单列处理。允许正常双主体设计，如 Generations；无法确认与明确冲突应区别呈现。
4. 恢复数据时先冻结正反图片、现身份、成本、估值证据。846–853 可利用正确历史背面；806/809 可依据源版面纠正互换；657 批需明确重建映射，并谨慎处理五张同款 Instant 的个体不确定性。306 等四张需要补充真实背面证据。
5. 图片校正后重新确认身份，再核验相关成本分配和估值；保留原证据和变更审计。未经核验不直接覆盖真实交易。

**Files likely to change**：`flatbed_service.py`、`flatbed_matching.py`、`flatbed_api.py`、`image_service.py`、`scanner_queue.py`、`apps/web/components/flatbed-batch-panel.tsx`；数据恢复另用带前值与图片哈希检查的工具。

**Tests to add or update**：上述三批真实样本、半列未翻面、补扫另一批覆盖旧背面、OCR 失败、同球员异产品、赛季/日期差异、正常原始正反面与合法双主体卡对照。恢复数据需验证单卡成本分配与旧证据保持可追溯。

## Evidence and limits

- 私有冻结资料、原图拼图、分段审核明细、SHA256 对比与源版面时间线位于 `.runtime/inventory-face-mismatch/`，未提交到仓库。
- `early-audit.json`：439 张；`middle-audit.json`：390 张；`recent-audit.json`：48 张，共 877 张。
- 未调用外部识别模型、未执行新的扫描或写入 API；离线复现使用历史原始信号。
- 未修复或部署，未运行应用全套测试；本次结果是调查证据与错误复现。
- 同球员同产品且无可辨识字段的实卡配对仍不能由这次初筛完全排除。
