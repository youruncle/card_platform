# Domain modules

- `catalog`：球员、品牌、系列和卡片定义
- `portfolio`：持仓与交易账本
- `valuation`：市场可比成交和估值
- `intelligence`：比赛数据、新闻、伤病和球队环境
- `scoring`：版本化评分模型
- `advisory`：AI 分析请求和投资建议
- `ingestion`：外部数据源适配器

领域模块不得依赖具体 HTTP handler 或某一家外部数据供应商。
