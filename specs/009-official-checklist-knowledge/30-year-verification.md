# 30年三厂商官方篮球清单验收 — 2026-10-05，知识 v12

冻结窗口为 **1996-10-04 至 2026-10-04**。本轮工程实现、可获取来源发布与部署完成；官方历史目录未穷尽，不能声称30年全覆盖。沿用 v11 的已发布来源，不重复导入旧事实。

| 厂商 | 已发现 | 窗口内或日期未知候选 | 已发布 | 候选成功率 | 失败 | 日期未知 |
|---|---:|---:|---:|---:|---:|---:|
| Topps | 58 | 58 | 57 | 98.3% | 1 | 48 |
| Panini | 447 | 446 | 443 | 99.3% | 3 | 0 |
| Upper Deck | 24 | 24 | 7 | 29.2% | 17 | 21 |
| 合计 | 529 | 528 | 507 | 96.0% | 21 | 69 |

Panini 2025-26 Select（官方日期2026-10-22）排除。日期未知候选独立标识，未以赛季、抓取日期或文章日期推定发行日期。e-Pack三个产品使用官方 ReleaseDate，明确限定为 e-Pack 上架日期，不声称实体卡首发日期。

## 新增发布与审计

- v12单次新增7个UD来源、1,913条篮球卡片事实：2016/2017 Goodwin Champions、2026 Metal Universe SE、2025 Allure SE、2016-17/2017-18 Turkish Airlines EuroLeague、2021 Premier Prospects Basketball。
- UD HTML只接受Category明确为Basketball的行；e-Pack以官方Basketball产品元数据与逐行EpackProductName/InsertName共同限定。跨产品Update、无明确运动分类、无年份及截断原文不入库。e-Pack目录4项目中，1件UDA签名照片纪念品排除。
- 原始HTML/API JSON、SHA256、表号/行号、原文行、官方产品归属证据、日期含义与获取方式保留。完整性失败不会计为成功事实。
- 分厂商/赛季清单保存在status.json的publishers.years。不可变已知来源目录：data/official-checklists/manifests/inventory-30-year-aad2b1298c31.json，SHA256=aad2b1298c31f9152b01157e1a0c98f36fc2dbd003fd438f15483e03a513ea7a。全窗口分母仍为null。
- 独立只读审查发现Base Set后缀误作parallel；已修复并以official-layout-v6-ud2重新生成附件。Fresh Foundation、First Look、Short Prints等仅保留section，不推测parallel。审查复核15条真实受影响样本及9项专项测试通过。
- 再次执行 --apply 仍为v12，新增来源0。总计458个版本附件校验通过，历史内嵌事实与附件均保留。

## 验证证据

- 相关后端回归163项通过；进度组件测试1项通过；TypeScript检查通过；worktree及实际运行目录Web生产构建通过。
- 全部1,913条新增事实逐条通过产品/赛季/球员/卡号查询，35个错厂商/产品/赛季/球员/卡号负样本拒绝；未自动填充平行或印量。6组样本通过实际AutomatedValuationWorker.resolve_identity_row和知识查询计划路径。
- PostgreSQL跨连接事务锁通过。实际发布隔离测试使用回滚事务，未纳入无关草稿；版本附件保留；相同版本复用运行时resolver。
- 发布后对现有627张卡片只读回放：171张匹配，3张可补充insert（170→Autographs，450→Acetate Rookies，586→Roses），12个负样本全部拒绝，附件错误0。新增UD来源未增加当前卡库匹配数；不能将来源样本回放说成实际持仓新增命中。
- 发布前后collection_item 627、card_definition 818、card_identity_version 897、valuation_snapshot 894行的全行排序SHA256完全相同：本轮未改持仓、状态、身份或价格。详见data/official-checklists/upper-deck-before.json、upper-deck-after.json、portfolio-replay.json、publication-verification.json。
- 仅部署本任务解析、索引、同步脚本、进度组件及其类型；原目录api.ts采用局部类型替换，未覆盖其他修改。API/Web服务重启后/health与/knowledge均HTTP200。
- 未执行有效管理员登录态下的浏览器端到端点击验证；浏览器控制连续超时。页面构建、组件行为与服务健康已验证。

## 未解决的官方来源缺口

- Topps：2022-23 NBL官方链接404；公开完整历史目录1996–2019缺口仍在。Ripped官方90年代特卡文章只作为线索，未拿到完整原文，不以搜索片段入库。
- Panini：公开目录只提供2009–2026共18个档案年，1996–2008不可确认穷尽。3份清单接口仅返回表头空CSV，沿用v11的原响应证据。
- Upper Deck：10份候选直连403；2018/2019/2020/2021 Goodwin的浏览器导出发生截断，已隔离，浏览器工具随后持续超时；2025 NSCC、2024-25 GR8 Moments缺少明确篮球分类；World of Sports Volume II缺少产品年份。17份均失败，不计成功。逐项URL与错误见catalog.json。
- Upper Deck旧Collectors-Zone、目录分页、WordPress接口和sitemap返回403；现代站点可见档案主要自2015年起，1996–2014完整官方目录仍无法取得。Fleer/Skybox仅在具体官方产品标题证实时作为来源作用域内别名，不将品牌历史归属一概推断。

## 恢复执行

使用已有运行目录数据库配置，运行download_upper_deck_checklists.py或download_upper_deck_epack.py恢复官方获取，随后sync_official_history.py --apply --deploy-root <运行目录>。截断文件需先重新取得完整官方原文；失败缓存不会作为合法附件发布。运行verify_upper_deck_extension.py、replay_official_knowledge.py、verify_official_publication.py验证。备份必须包含data/official-checklists/artifacts；工程100%仅表示本轮交付与已知候选处理完成，不表示历史清单覆盖100%。

## 后续UD修复（知识v13）

2026-10-05已新增恢复13份UD来源、7,269事实，UD20发布/23符合范围候选；全局520/527=98.7%。详情及剩余3项见../../.specify/bugs/ud-source-recovery/verification.md。本文件上文为v12历史验收，不代表当前最新统计。
