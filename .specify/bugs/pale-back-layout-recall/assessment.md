# 浅色背面漏检

用户截图：`/Users/song/Desktop/截屏2026-10-11 00.14.23.png`。
真实原图：`data/flatbed-scans/c6e6987e-eec0-40a0-a9de-92ce5d51a7cc/6eb10a08-f4bb-4953-8f7f-01e355c8bf4f/5157cbd8-5734-4214-bbd4-2200b132ec75/original.png`，2480×3507、300 DPI。

实际七张背面仅返回五个框。前景分量将左侧相连卡套按高度分成四个框，另找到右上卡片；数量、面积和重叠检查误认为布局完整，跳过网格补检，漏掉右侧中间和下方浅色背面。更早的背面 revision 1 还返回了四个框。

最小修复：完整前景布局必须同时通过既有剩余图像结构检查；检测框之外仍有分布式文字、边缘或颜色时，继续既有多路补检。不使用正面坐标制造背面区域，不更改身份识别和估值。

真实原图测试先失败（5 != 7），修复后返回七个独立区域。相关五个测试文件 60 passed。七个裁切质量报告均 usable；六个带 source_edge_contact warning，原图右侧确实截到扫描边缘，不能恢复原图之外的内容。尚未验证身份识别和自动配对。

运行验证：本机 API 已重启，/health 返回 ok；调用既有 store_sheet 从不可变原图生成背面 revision 3（6954c3c1-584b-454c-910c-e34c8b7fb6ce），实际 SAM 路径返回七个区域，七个 usable、零质量 error，检测器 physical-object-layout-v23+sam2.1-tiny。旧 revision 保留。重新生成配对候选，missing_front/missing_back 均为空，requires_review=true；部分 OCR 身份冲突与边缘警告仍需人工检查，未提交录入或运行完整身份识别。
