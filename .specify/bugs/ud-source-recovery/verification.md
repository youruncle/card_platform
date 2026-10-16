# UD失败来源修复验收 — 2026-10-05

原知识v12有UD24候选，7发布、17失败。修复后知识v13：**20发布、1非篮球排除、3失败**，已知符合范围候选成功率20/23=87.0%。新增13来源、7,269事实；UD现有20份附件共9,182条事实。全局520/527=98.7%（529发现、2排除、7失败）。不声称完整30年覆盖。

## 已修复

- 2018–2021 Goodwin完整下载：107、153、332、828条；4个截断原文件以.invalid-SHA256前缀命名保留，坏缓存重取，校验结构完整后原子替换。
- 2022、2024、2025、2026 Goodwin：1,032、743、183、424条。
- 2021、2022-23、2023-24、2024-25 Metal Universe Champions：973、845、1,131、516条。2022-23和2024-25的错误URL按官方目录更正，保留原source_key与原URL证据；新旧发现记录归并，未增加重复产品。
- 2025 NSCC：2条（PC-2 Michael Jordan、VIPA-DF De’Aaron Fox），逐行绑定并验证官方篮球分类证据；其余80行隔离，不声称整份混合清单已全部分类。
- 2024-25 GR8 Moments：按官方NHL许可及已核实产品范围排除。排除规则不泛化到同样携带NHL许可的混合产品；2021 Metal Universe的篮球行保留。
- 同步器保留非篮球排除状态；缓存键包含补充证据校验值；校验引用原文、源行和球员，禁止推导链替代明确官方分类。

## 验证

- 按apps/api/pyproject.toml配置完整测试：2,156通过、67跳过、1条Starlette弃用警告。随后补充canonical-only断点恢复分支并运行恢复专项11通过；最终清单/恢复/历史相关29项通过。
- 全9,182条UD事实查询验证，100个错厂商/产品/赛季/卡号/球员负样本拒绝；19组实际估值身份解析与查询计划样本通过。
- v13新增来源13；幂等重跑仍v13、新增0。只发布指定官方来源，不发布其他草稿。
- 627张现有卡片只读回放：174张匹配（v12为171），新增display_id 344、388、499；3张可补充insert保持不变；12个持仓负样本拒绝，附件错误0、持仓/估值写入0。
- 发布后附件与受保护表验证结果见同目录补充记录及data/official-checklists/ud-recovery-after.json。
- 只部署official_checklists.py、sync_official_history.py、download_upper_deck_checklists.py及本任务测试/证据。未修改前端和估值worker；知识运行时按版本自动刷新。API /health、Web /knowledge均HTTP200。未执行管理员登录态浏览器端到端验证，浏览器控制仍超时。

## 剩余3项

1. 2026 Upper Deck NSCC：多轮限速重试后仍HTTP403，中途也出现TLS EOF；无完整原文，未导入。
2. World of Sports Volume I：官方产品页已证实产品年份2025并链接对应清单，但清单仍HTTP403；不能用产品介绍代替逐卡清单。
3. World of Sports Volume II：完整混合清单存在，但原文无明确产品年份和逐行运动列。e-Pack元数据仅说明2026-07-15线上上架及2026版权年，不足以证明实体卡产品年份；保持隔离。

三项均保留官方链接、实际错误和证据。2026 NSCC与Volume I需官方访问恢复或取得可访问的官方清单附件；Volume II需官方产品年份及逐卡运动证据。不用第三方复刻或猜测值补齐。

不可变更新目录：data/official-checklists/manifests/inventory-ud-recovery-e9de8f8c3aed.json；旧30年目录继续保留。

最终发布后验证：471个版本附件SHA256校验通过；无关草稿隔离与历史附件保留测试通过并回滚。collection_item 627、card_definition 818、card_identity_version 897、valuation_snapshot 894行的发布前后全行SHA256完全一致。
