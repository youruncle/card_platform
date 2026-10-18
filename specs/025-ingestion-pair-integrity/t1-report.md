# T1 OCR 与匹配验证

实现提交：`f0b5b10b`（仅匹配、信号提取、新纯单测及去标识真实回归数据）。

## 行为

- `extract_pair_signals(text)` 保留 `ocr_text/ocr_tokens`，区分产品、insert、完整球员标签、明确发行赛季、卡号和独立限编印记。使用项目内固定球员词表和本地文本规则，不调用模型或网络。
- 不再把正文中第一个年份当作发行年份；纪念日期、版权年份、统计比例和三段日期不产生年份/限编锚点。单独的 Prizm 箔面标识不能覆盖 Select 等明确产品。
- 历史区域在匹配时重新读取保留的原始 OCR，忽略旧 `year`，重新判断旧卡号/限编字段。因此已保存的错误元数据也进入修复路径，不只影响新扫描。
- 缺身份信号的 OCR 分数保持中性 0.5，同时明确 `identity_status=unknown` 并要求审核。OCR 不再充当第二份 recognition 分数；仅存在显式独立识别分数时才给该项权重。
- 新增 `identity_status/identity_reason_codes/identity_ambiguous`；明确产品/insert/卡号/主体/印编/发行赛季冲突保留 conflict。重复同款歧义不能把已知 conflict 降为 unknown；合法多主体设计在共同产品、编号和设计支持下进入独立设计审核，不因姓名不同直接判错。
- 同款、相同编号、缺个体限编证据的重复背面提示 `same_design_copy_unverified`，不能把位置配对解释成实体身份已核验。

新增原因码：`ocr_identity_unverified`、`ocr_product_conflict`、`ocr_insert_conflict`、`ocr_card_number_conflict`、`ocr_player_conflict`、`ocr_serial_conflict`、`ocr_season_conflict`、`same_design_copy_unverified`、`multi_subject_design_requires_review`。原有 `conflicts` 中保留总类 `ocr_identity_conflict` 和裁切/布局提示。

## 实测红绿证据

初次新增回归：17 failed。真实657冻结区域仍选 `mirror_y`；同球员异产品仍按错误位置配对；新 unknown/独立 recognition 字段不存在；新提取模块尚不存在。实现后26 passed。

继续加入边界回归后4 failed、18 passed，分别复现重复背面使 conflict 被降级、Prizm 单标识误判产品、不同明确发行赛季未形成身份冲突、带空格日期/统计比例被当作限编。修正后31 passed。

最终在工作树执行：

```sh
PYTHONPATH=apps/api/src .venv/bin/python -m pytest \
  apps/api/tests/test_flatbed_matching.py \
  apps/api/tests/test_flatbed_pair_integrity_matching.py \
  apps/api/tests/test_flatbed_pair_signals.py \
  apps/api/tests/test_flatbed_service.py \
  apps/api/tests/test_flatbed_back_geometry_independence.py \
  apps/api/tests/test_flatbed_api.py -q
```

结果：**50 passed in 14.03s**。四个修改/新增 Python 文件的 Ruff 检查和 `git diff --check` 均通过。按任务范围没有运行整个大型项目测试集；数据库、模型、客户端构建和部署不属于本 T1 验证，留给集成任务。

## 原始私有保留数据的内存重放

未修改数据库或源图，读取原始 `provenance.json` 逐区域重放：

|批次|结果|身份状态|需要审核|
|---|---|---|---|
|657批8张|`identity`，8个原始位置配对全部正确；产品级错误 `mirror_y` 被消除|7 unknown、1 consistent|8/8（还包括原有裁切提示）|
|806/809所在8张|仍生成几何位置候选，未冒充纠正无 OCR 的局部交换|8 unknown|8/8|
|846所在8张|保留几何 `mirror_x` 候选，未冒充旧正面与另批背面已核验|8 unknown|8/8|

仓库真实回归 fixture 仅保留匿名区域编号、边界、原始文本和必要图像统计信号，无账户ID、任务ID、源路径、URL或密钥。

## 边界

OCR匹配结果仅是平板配对提案，不替代 T2 绑定当前图片的联合视觉门禁。OCR缺失时无法恢复806/809的真实局部交换，无法仅凭版面识别846整批跨批污染。产品词表和完整球员标签识别有限，未覆盖产品保持未知；未知不会冒充一致。同款 Instant391 的五个实体没有独立标记，无法证明各副本一一身份。
