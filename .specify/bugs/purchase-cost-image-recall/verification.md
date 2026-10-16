# 修复验证

日期：2026-10-14。

## 最终针对性检查

| 检查 | 实际结果 |
| --- | --- |
| 临时 PostgreSQL、全部迁移、11 个相关后端测试文件 | 343 passed，1 个 Starlette 弃用提示；进程退出 0 |
| 前端全部 `tests/*.test.cjs` | 145 passed，0 failed |
| TypeScript `tsc --noEmit` | 通过 |
| Next.js 生产构建及 standalone 资源同步 | 通过 |
| 新增测试和 purchase_cost_inference 的 Ruff 检查 | 通过 |
| `git diff --check` | 通过 |
| 重启当前本机 API、Web 并持续健康检查 | API healthy、Web healthy |
| 独立只读代码审查 | 最后一轮未发现本次范围内剩余 Critical/Important |

隔离数据库命令：`CARD_PLATFORM_TEST_PORT=55436 bash .runtime/cost-repair-test-e2e.sh`。测试范围为 purchase_cost、purchase_cost_inference、purchase_cost_search、purchase_cost_postgres、purchase_cost_recall_repair、purchase_cost_title_correction_postgres、purchase_cost_bundle、purchase_bundle_postgres、cardhobby_account、cardhobby_account_postgres、image_service。

前端命令：在 apps/web 运行 `node --import tsx --test tests/*.test.cjs`。没有运行移动端套件、云端部署检查或整个卡库重新识别。

## 失败到通过的证据

- 名单、仓储后缀、日期角色、图像标题纠错及补充检索均先保存失败回归，再修复。
- 严格同款加另一个 compatible、低分 same、几何不足、缺独立观察或弱 different：新增数据库用例先出现 5 failed、6 passed，修复后纳入最终全通过范围。
- 已有门禁的重试丢失身份字段：数据库用例先出现 1 failed、14 passed，修复后通过。带原有年份错标且丢失产品字段的反例也先失败再修复。
- 真实限编 `/8/50` 保留并正确解析分子／分母，不以放宽限编规则消除误报。
- 缺图、多图、数量大于一、缺目标图、不确定观察和恢复旧 same 缓存均不能自动入账；部分订单范围重试不能解除门禁。完整范围获得严格文字结论或有效图片同款／不同款结论后可解除。
- 现有多人同款需要用户选择、手绘原作保护、原纯文字筛选、账户隔离、成本重复保护与打包分摊回归全部通过。

## 两个真实样本

- 862：通过现有采购成本服务录入一笔买入，卡价 123.88、运费 2.50、合计 126.38；最终只读账本核验金额与来源存在，修复后的真实卖家标题可直接通过名单文字匹配。
- 875：最终版本重新读取产品相关的 4 个完整订单，找到唯一可自动匹配候选，卡价 19、运费 5、合计 24。图片观察采用现有服务及绑定资产检查点。回放前后交易记录逐字段相等，用户新录入的 20 元人工成本完整保留，未执行刷新写入。
- 没有批量覆盖其他卡片的成本或身份。日期语义保护作用于后续识别；这两张历史记录的卡号未通过全卡确认方式改写。

包含私人订单快照和资产证据的详细结果只保存在 .runtime/cost-repair-875-862，未放入规格文档。

## 全量检查局限

修复过程中的一次全量后端检查：157413 passed、127 failed、139 skipped。失败名称保存在 full-suite-failures.txt；没有因为针对性检查通过就宣称全量通过。

三个 test_recent_valuation_roster 失败已做对照：仅去掉本次 player_aliases 增量，两边均为 3 failed、17 passed，失败名称一致。其余 124 项尚未逐项归因，不能统一归为既有问题。另一次全量隔离数据库检查中止，结果不计为通过。

两个真实样本不足以估计全库准确率。本次没有测量批量识别率、向量模型收益、延迟改善或模型费用变化；这些需要固定分层样本集与前后对照。

后续路线与范围见 report.md。
