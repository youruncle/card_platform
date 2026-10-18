# Bug Fix: 正反面数据恢复与录入完整性优化

- **Slug**: inventory-face-mismatch（沿用上下文）
- **Fixed**: 2026-10-19
- **Assessment**: ./assessment.md
- **Status**: partial — 数据恢复完成；录入代码优化和新版本发布正在执行。

## Summary

线上、本机均恢复16张已确认异卡错配的背面，按原图修正混合身份字段；4张没有可验证真实背面的记录清空错误背面引用并标注待补扫。另将同批658的“1 of11941”从实卡限编改回印量含义，保留11941印量；659保持原状。用户随后要求实施工程优化并发布新版本。

## Changes

| 文件/资料 | 变化 | 说明 |
|---|---|---|
| `apply-repair.py`、`prepare-repair.py`、`freeze-repair.py`、`verify-repair.py` | 添加可复核恢复工具 | 默认回滚；显式--apply、所有权、当前值/图片SHA256、确认版本与幂等检查 |
| 私有恢复清单和图片 | 添加 | `.runtime/inventory-face-mismatch/repair-stage/`；原文件不覆盖、不删除 |
| 线上与本机21条目标记录 | 更新 | 16配图+4错误背面引用+1编号纠正 |
| 身份版本/变更审计 | 追加 | pending、字段级证据与恢复ID，不冒充全卡人工确认 |
| 目标旧估值 | 标记不兼容并排队重算 | 保留原值与来源，未将新身份套用旧估值 |

## Verification

- 线上、本机各21条，先在真实数据库完整执行并回滚；交易和订单分配逐字段一致。
- 恢复验证脚本在修改前报告113项预期差异；修改后线上、本机各21条，差异0。
- 原40张业务图片仍保留；恢复图采用新文件名，避免原URL缓存覆盖。
- 仅809有历史购入：人工110.00元，保持原样；未自动覆写任何交易。
- 两环境实际设置 purchase_cost_worker_enabled=true。
- 本机恢复清单再次执行返回already_applied，不重复创建版本或变更。
- 缺少真实背面的306、308、311、312仍需补扫；未替换为目录图片或另一份实卡。

## Deviations from Assessment

用户明确先修复这批数据，再要求优化代码和落地新版本，故先完成数据恢复。658与659是五张同款Instant中的两个已同产品配对，不猜测实体归属；只纠正658有直接原图依据的假限编，659完全保留。

849是合法双主体CoreyBenjamin/KobeBryant#61。依据源图及[TCDB目录](https://www.tcdb.com/Checklist.cfm/sid/9257/1998-Collector%27s-Edge-Impulse)、[产品实卡记录](https://www.ebay.com/p/28056522788)恢复。847的142号进一步由[Topps官方Updates目录](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/Final_CheckList_26CUBK.pdf?v=1783360479)核对，避免误用Chrome主系列中同号其他球员。848/850的GenerationNow编号与赛季由[Topps官方目录](https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Topps_Basketball_Checklist.pdf?v=1759329649)核对。

## Follow-ups

工程优化、真实样本回归与网页/手机/API发布使用同一套SpecKit规格和任务；完成后更新本记录。
