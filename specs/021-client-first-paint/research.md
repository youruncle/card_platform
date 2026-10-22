# Research

Decision: 复用019的membership快速路径；不重写后端估值投影。
Evidence: Mobile已有snapshot_mode=membership；Web refresh首次include_summary=true导致后端全库list_items。
Alternatives: 仅并行summary仍可能争用同一后端且不能减少默认full快照计算；故先确保首批投影独立，再启动统计。金额排序保留full准确语义。
