# 卡淘实际买入价 Skill Implementation Plan

> 使用executing-plans在当前目录逐项实施；不委派、不创建新worktree，保留全部既有修改。Spec Kit为唯一规划源。

**Goal:** 同一云端账户在手机和电脑ChatGPT查询本人卡淘买入价及证据。
**Architecture:** 既有PurchaseCostService增加显式preview_only分支；MCP持久任务调用卡淘账号搜索与相同核图规则，不触发confirm或自动续写；独立Skill负责消歧、恢复和结果解释。
**Tech Stack:** Python/FastAPI/psycopg/PostgreSQL、官方MCP SDK1.30.0、既有ChatGPT Skill ZIP。
**Spec:** [spec.md](spec.md)

## Global Constraints

沿用现有账号隔离、云端主库、成本worker=true、deepseek-flash显式禁思考及独立百炼搜索；任务最多3次恢复、关键词源于目标实卡、只返回候选证据、不暴露凭据。真实收藏仅查询。

## 实施顺序

1. 在test_mcp_purchase_cost.py先写唯一匹配只查不写、旧入口照常确认的失败回归；purchase_cost_service.py增加preview_only=True分支并禁止该分支安排automatic_purchase_cost。
2. migration0089新增查价任务；mcp/purchase_cost.py完成start/get/list和租约worker，main/server只接本功能。临时库覆盖撤销、跨账号、并发、同键异参、结果变化、账号阻塞和异常脱敏。
3. plugins/card-manager/skills/cardhobby-purchase-price建立Skill/UI元数据，实际校验和打包；新增能力提示由get_capabilities核查，不编造工具。
4. 定向及相关回归，增量发布API和迁移，实际工具调用/账号安装；记录真实结果与跨端缺口。

## Review Focus

查询误触发confirm或后台自动续写；他人订单泄露；撤销与长请求交错；同款多件/包价误报单卡价；已有成本被刷新；客户端缺新工具时编造已完成；真实卡片识别不确定仍自行挑单。
