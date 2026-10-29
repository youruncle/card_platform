# 最近扫描未入库（2026-10-30）

云端最近两批共16个scanner任务：5已入库、9识别完成但等待核对、1模型失败、1运行中卡住。任务原图均存在，非上传丢失。私有任务明细、原图缩略图、哈希和原始返回保存于忽略目录`.runtime/scan-intake-20261030/`。

- 真实Ja Morant Splash! #15原图复现模型只返回pair_validation.status/evidence，漏front/back；Pydantic填默认unknown，服务端配对门禁正确拦截。不能通过放宽门禁解决。
- 同一真实样本另一次返回memorabilia_types:null，CardRecognitionResult报ValidationError；原worker只暴露all configured vision model tiers failed。历史失败调用没有保存原始响应，因此不能断言历史那一次必然是同一字段。
- Clint Capela #161任务停在running、attempt=1、无result/collection_item_id。旧恢复只在启动执行；启动时未达到15分钟阈值的孤儿任务之后无法再次检查恢复。
- 独立查看16组照片未发现本批错配。待恢复记录中的Ja Morant把Court Kings误识为Crown Royale，Nikola Jokić漏读背面No.4；按当前原图核对。

Tre Johnson III #118的发行赛季由2025-26 Topps Pristine目录交叉核对；Sevilla #121由2023-24 UEFA Club Competitions目录与照片核对。未将纪念日期/版权年直接推为发行赛季。

外部来源：[Pristine目录](https://www.tcdb.com/ChecklistByLastName.cfm/sid/683127/2025-26-Topps-Pristine)、[Pristine交叉来源](https://www.beckett.com/news/2025-26-topps-pristine-basketball-cards/)、[Sevilla目录](https://www.checklistinsider.com/2023-24-topps-uefa-club-competitions)。Topps历史PDF直接回读404，TCDB后续回读403，已保存实际尝试；采用可读取目录交叉证据，不称官方原件已完整回读。

## 第二轮复核：失败可见性与门禁（尚未实施的新改进）

用户追问为何看不到失败case及门禁是否过严。只读核查与纯函数受控复现确认：

- 网页卡库占位仅覆盖queued/running，转failed或succeeded但未关联库存后消失。后台队列只在非Canon、scanner模式显示；默认Canon页面不展示，整版提交回调还会关闭弹窗。
- 未入库的succeeded任务被显示成球员名或“识别完成”，按钮叫“查看旧任务”，没有明确表达“尚未入库，待你处理”。状态只表达模型执行完成，未表达业务完成。
- 手机capture任务接口按require_confirmation过滤。自动扫描识别失败时该值可能仍false，故手机任务页无法覆盖这类失败；不应按是否需要人工确认决定任务是否可见。
- 网页扫描列表默认30条、无分页及失败优先保障；读取异常有静默忽略路径。重试直接清空错误/结果并覆盖当前状态，没有用户可见的逐次尝试历史。
- 当前账户只读回查：最近两批16/16已关联库存，当前failed=0；另有1条照片录入待确认草稿。恢复后的“当前失败为0”不等于用户能查看曾经失败的过程。其他账户历史任务不算该用户待处理项。
- 受控正常样本仅移除正面product_name即从consistent变unknown；将同一卡号两面写成No. 4与4会判card_number_conflict；Nikola Jokic与Nikola Jokić会判subject_conflict。这些是纯函数构造反例，不能冒称本批历史失败一定由它们导致。
- 自动入库payload在发行年未知时会使用copyright_year，再缺失则使用当前年份；未知is_autograph等布尔字段经bool(None)变false。两项均由纯函数直接复现，尚未修改，也未审计其历史影响。

改进优先级建议：先统一网页/手机任务可见性、明确待确认/失败状态并保留尝试历史；同时去掉无依据身份兜底。再按明确冲突、证据不足和可选字段缺失细分门禁，增加合法同义写法归一化及真实正反面样本回归。明确错配继续阻止合并，但原图与待处理任务始终可保存、查看和校正。需要跨端实现时共用一份Spec Kit规格/计划/任务，本次复核未擅自调整新业务门禁。
