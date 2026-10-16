# Data model
field_assessments: 字段→{status, source, evidence[], issues[]}。状态 unknown/proposed/verified/conflicted；来源 none/visual_model/local_ocr/catalog。独立目录核实仅复用现有候选校验。
identity_storage_binding: 定义 UUID、限编/评级、原图 SHA256。生成端是扫描服务；消费端是采购服务。任何绑定不符均不消费旧字段状态。消费仅撤销存储兼容值，不增加新身份值。

catalog_verified_fields：服务端强来源网页核验后实际应用字段的值；enrich 与 Qwen 入口清理提供者自报内容。字段评估仍需该值、已应用证据前缀和支持同一值的来源候选一致，才标 verified。限编实物分子不由目录核实。

local_ocr_observed_fields：本机读取的值仍为 proposed，不表示目录核实。它可说明新卡号替换了旧日期/位置误读，但不能单凭来源清除年份角色问题。传记/大学赛季首先在 OCR 提取时排除，最终 sanitizer 仍独立检查。
人工 confirmed 且未被 rejected 版本撤销时，不消费旧识别的 unknown 投影；同值确认也有效。存储绑定覆盖 set_name、print_run、签字类型/铭文、Patch 与物料属性，防止定义内可变字段遗漏。
