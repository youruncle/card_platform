Latest verification: [20-year scope, v11](history-verification.md). Earlier stage records below are retained for audit.

# Verification — 2026-10-04

任务未完成：工程进度 75%，T002 全目录/日期核实和 T008 全量验收仍待完成。没有人工审核队列。

## 已实际交付

- 官方 Topps 目录发现并保存 32 份篮球 PDF；31 份自动入库发布为 v8，1 份已有官方日期证据确认早于 2025-10-04 而排除。
- Panini 官方目录 HTTP 403；两次早期浏览器初始化失败及本次浏览器导航超时。保留 8 个官方产品线索，但尚无其 checklist。目录非穷尽，不能把 40 个候选当真实全量分母。
- 已发现候选发布比例 31/40=77.5%；全量覆盖率未知；31 个候选缺少可靠发行日期（包括 Panini）。未知日期没有用赛季或文章发布时间代替。
- PDF/XLSX 解析保留原始文件、全文、卡片行、页码/行号和 SHA256；自动隔离无法可靠结构化的行。当前31份含24,200条结构化行、407条未结构化/非身份行，实体按规范键去重。文件发布不代表其每一行都已成功结构化。
- 作用域索引按产品、年份、球员、卡号匹配，只在候选一致时填缺失 insert，不猜平行、限编、签字等物理版本信息。
- 运行时搜索和估价身份都消费官方事实，带知识版本与官方页码证据。worker每个任务检查发布版本，未变更时复用resolver。
- 自动发布以旧published快照为底，仅替换本次官方来源集合；其他草稿保持原状。重新解析同来源会原子失活旧card/insert，避免旧错误章节留下冲突实体。
- `/knowledge` 新增工程进度、候选发布比例、逐产品状态、异常数和来源；5秒刷新，接口保留管理员鉴权。

## 真实回放与验证

- 对现有627张卡片只读回放，52张命中官方事实，1张获得缺失字段补全：ID586，Tracy McGrady，Signature Class，R-5 → Roses，官方PDF第10页第14行。查询计划包含Roses；数据库持仓和估值没有被回放脚本修改。
- 新构造未知产品单测证明此补全依赖知识库记录，而非先前Signature Class专用回退。
- 重复执行sync保持v8、0产品重复导入。
- PostgreSQL临时事务实际加入无关active draft，再运行官方来源发布，成功且草稿仍为draft，快照不包含该草稿；测试事务完全回滚。
- 独立审查发现并修复断行卡号、SELL SHEET误标、黏连卡号污染、旧事实未撤销、无关草稿阻断发布。二次审查确认335条疑似黏连行自动隔离、残留为0；官方原文全部保留。
- 最终后端相关回归169项通过；进度组件测试1项通过；TypeScript检查和生产构建通过。首次构建遇到旧Next缓存路径失效，保留旧缓存后重新构建通过。
- 本地API/Web已重启并健康。浏览器能打开知识库页面，但该浏览器没有有效登录会话，实际登录态下的进度展示未做端到端验证；组件测试验证75%、77.5%、未知全量覆盖率和异常提示。

## 可重复命令

```sh
.venv/bin/python scripts/sync_official_checklists.py --apply
.venv/bin/python scripts/sync_official_checklists.py --discover
.venv/bin/python scripts/verify_official_publication.py
.venv/bin/python scripts/replay_official_knowledge.py
.venv/bin/pytest -q apps/api/tests/test_knowledge*.py apps/api/tests/test_valuation_worker.py apps/api/tests/test_signature_class_roses_recall.py apps/api/tests/test_official_checklists.py
cd apps/web && node --import tsx --test tests/official-checklist-progress.test.cjs
```

`--discover`重新抓取官方入口证据；发行范围使用冻结catalog，避免把目录里的所有历史赛季误认作近一年产品。它不会自动扩张本任务日期范围。网络限制不会通过手工审核解除，也没有安排无人值守的未来重试。

## 尚未完成

- Panini清单获取、近一年完整目录与发行日期覆盖。
- Topps未定日期候选的发行日期证据及官方目录缺漏确认。
- 407条隔离/非身份行中有效卡片行的进一步自动恢复；不能宣称清单结构化100%。
- 上述来源齐全后的全量回放验收。现有回放证明身份补全改善，不能推断估价金额误差已经下降；未批量重估既有持仓。


## 用户截图补充与访问结论修正

用户2026-10-04截图明确显示Basketball / 2025 / Panini / Court Kings (25-26) 的 DOWNLOAD FULL CHECKLIST 按钮；另一张为Cookie同意弹窗。之前未完成关闭弹窗后的交互验证，不应将脚本403和自动化浏览器超时概括为网站不可用。本次重新导航及读取页面仍超时，尚未验证弹窗关闭或文件下载；工程进度保持不变。

## Panini API 接入及最终回归（2026-10-04）

- 公开目录 API 已验证，无需浏览器逐个下载。全部18年份扫描：447个产品，429个在窗口外，18个在窗口内；下载18/18（100%），130615行。下载器 scripts/download_panini_checklists.py 可重跑、缓存文件、记录阶段百分比。
- CSV 解析保留129642条卡片记录，973条带完整产品标题的可疑跨产品记录保留原文并自动隔离。入库无需人工校验。
- 首轮已有卡片回归发现 Base Plaid / Base Cracked Ice 被误补为insert；已补回归测试，official-layout-v5 阻止所有Base变体补成insert，未知变体后缀也不猜测。
- 最终发布知识版本v10，49个来源产品已发布（Topps31、Panini18），1个已明确窗口外排除。Panini窗口内入库18/18（100%）。整体工程90%，目录日期任务T002仍未完成；Topps23产品发行日期未知，完整覆盖率仍不报告100%。
- 最终真实回放627张已有卡片：64张匹配官方知识，较仅Topps时52张增加12张；唯一缺失insert补全为ID586→Roses。ID17/173不再错误补入Base变体。回放数据库写入0，不改变持仓和估值金额。
- 相关知识/清单测试55通过；Python编译检查通过。此轮无前端修改，未重复执行前端测试。当前估值运行时沿用已实现的版本自动刷新机制。
