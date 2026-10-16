# Research
- 复用knowledge_source/evidence/entity/import_batch与版本表，不建立第二套知识库。
- 当前发布v4无card实体，normalize_identity仅别名替换；新增scoped索引是必要链路。
- 来源页curl被站点返回错误页时，不认作空目录；可使用已获取的官方web工具目录证据作为此次发现快照，保存transport说明。后台采集仍需显式失败状态。
- 官方发布日期缺失不从赛季或CDN修改时间推断发行日期；未知状态单列，同时保存官方清单知识。
- 自动批准严格限于本任务官方清单事实，用户授权覆盖旧人工审核流程；不批量批准其他草稿。

## Panini 官方接口验证（2026-10-04）

网页 Cookie 弹层关闭后目录可用。官方前端 `8951.19322ca68041cead6841.chunk.js` 和 `index-d91a9049.af497719fb921d6796c6.js` 指向公开 API `https://support.paniniamerica.net`，无需登录、Cookie 或浏览器批量操作。

- POST `/replacement-card-selection`：JSON 字段 activity/year/brand/program/card_set/card/replace_wo_inventory/from_frontend。Basketball activity=9；逐级获取年份、品牌、产品，产品带 `release_date`。品牌级查询 from_frontend=2，其他层级为0。
- POST `/get-physical-checklist`：相同 JSON，program 使用官方目录返回的 ID，返回 CSV。
- GET `/get-product-checklist?sport=Basketball&year=2025&brand=Panini&program=Court+Kings+%2825-26%29`：名称参数版本，实测与 ID 导出相同 CSV。
- 已遍历全部18个年份、447个篮球产品；18个发行日期在2025-10-04至2026-10-04内，429个排除。18份下载成功，130615行。证据与阶段百分比：data/official-checklists/panini/progress.json。
- 官方导出本身混入部分带完整其他产品名称的 Instant 卡组；973行保留原文、自动隔离，不创建可能错误的卡片身份。不引入人工环节。
- CSV 的 SEQUENCE 保留为 serial_total；明确识别的颜色/版本后缀存为parallel。Base及其变体不可补成insert；未知后缀不自动猜测。

## 30年扩展研究（冻结截止2026-10-04）
- 最新基线v11：500已发布来源，529当前发现候选（含24个UD来源）；仅增量处理。
- UD官网 https://upperdeck.com/checklists/ 的当前筛选年份为2015–2026，未提供Basketball分类，Multi-Sport表格逐行Basketball才接受。Skybox/Fleer等品牌以官网品牌目录为归属证据，不能反推2005年前Fleer由UD发行。
- 旧Collectors-Zone、WordPress API、目录分页、sitemap直接请求403；部分现代页面浏览器可读。归档格式区分HTTP HTML与browser_dom_table_snapshot；截断导出拒绝。浏览器工具随后连续超时，未解决来源保留失败。
- e-Pack公开Basketball目录4项，其中3项篮球卡、1项UDA签名照片（排除）。Store/api/Epack/Products提供ReleaseDate，但明确只证明e-Pack可用日期，非纸卡原始发行日。GetChecklist用EpackProductRefId（不是ProductId），逐行EpackProductName校验。
- Topps Ripped官方 https://ripped.topps.com/2024-25-topps-chrome-basketball-retro-inserts/ 搜索发现1996–97 Youthquake等历史子清单；HTTP403，尚未取得完整可校验原文，不以搜索摘录代入。历史完整产品档案仍缺。
- 混合运动清单没有运动列（World of Sports、部分NSCC）自动隔离，不能由球员名字猜运动。没有产品年份不从文章发布日期推断。
