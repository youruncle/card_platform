# Search API / UI Contract

GET /v1/portfolio/items/page?page=1&page_size=30&search=<URL-encoded JSON SearchFilters>

无 search 或空 {} 沿用现有路径。错误 JSON/字段/范围返回 422。owner 仅来自登录 session，忽略伪造 owner_id；回收站不参与。总数为匹配数，页码越界钳制到最后一页，零结果 total_pages=1。

UI: 核心持仓下可见搜索框 + 搜索按钮 + 高级筛选按钮；输入提交后请求服务端，中文组合输入时不意外提交。筛选草稿提交应用，已应用标签可删除；清空重置所有条件和页码。首次提交/改条件清空旧选择；每页最多 30 条；现有后台刷新保留条件。查询失败有可重试错误，不将旧结果伪装新结果。
