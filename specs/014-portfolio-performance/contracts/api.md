# 接口兼容性

原 HTTP 路径、响应模型、鉴权及数据所有权不变。

前端新增 getPortfolioPage / getPortfolioSummary 调用接口。筛选列表不带 include_summary；无筛选且需要新汇总时可带 include_summary=true。旧 getPortfolio 保留以兼容其他调用者。辅助失败不使核心列表/详情失败，需在 UI 单独呈现重试。

图片上传仍使用已有单面端点；并发前后端共同保证每侧仅更新自己的列，任一失败不消费扫描任务。
