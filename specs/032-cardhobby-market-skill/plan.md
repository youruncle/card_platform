# CardHobby Market Skill Implementation Plan

**Goal:** 独立skill通过云端只读工具完成卡淘检索、实际核图、审核和证据估值。
**Architecture:** 新增独立JSON研究表及MCP模块；复用公开商品页解析和受保护附件下载。每次搜索一页，同步有界网络请求，无新后台worker。图片审核由使用skill的多模态模型完成，来源明确为模型判断。
**Tech Stack:** FastAPI/Python、PostgreSQL、MCP、现有ChatGPT skill安装渠道。
**Spec:** spec.md

## Global Constraints

保留账户隔离及撤销检查；原始响应私有存储；不写账本或正式估值；生产成本worker持续开启；视觉DeepSeek及独立百炼配置保持。

## Review Focus

空壳响应、跨账户、同幂等键异参、并发搜索丢失、图片/审核摘要过期、未知运费、重复实卡与不同品相。

## Task 1: Deterministic evidence and valuation

Create market_sales.py and test_cardhobby_market_sales.py. Interfaces: normalize_row(row), build_report(target, candidates, reviews, has_target_images). Tests first cover sold status, positive bids, finite amounts, past dates, null shipping, duplicate images/serial, three independent sellers, missing target fields, unreviewed candidates, stale evidence. Run targeted pytest RED then GREEN.

## Task 2: Durable cloud tools

Create migration0094 and mcp/market_sales.py. Register start/search/inspect/review/get/list tools in mcp/server.py. Typed identity and review inputs; run state owner scoped and idempotent, append-only request captures, immutable target images. Inspect returns actual inline image plus evidence digest. Tests use disposable manager_test_* DB, fake transport and actual image bytes; verify grants/revocation/concurrent updates and no business writes.

## Task 3: Skill and release

Create plugins/card-manager/skills/cardhobby-market-valuation and independent zip, install personal copy. Baseline/forward behavior checks plus quick_validate. Run relevant MCP/parser regressions and ruff. Review whole change. Deploy additive migration and overlay API image based on running production, preserve runtime settings and all unrelated code. Install independent cloud skill using existing account UI, refresh tools if supported, run real read-only search/image/report and record limitations.
