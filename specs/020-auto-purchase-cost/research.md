# Research

Decision：复用共用仓储和服务端卡淘账号搜索。
Rationale：网页hook依赖页面生命周期；手机无该hook，后端可保证一致与持久运行。
Alternatives：前端入库后自动点查询会在离开页面后停止；复用批任务会扫描旧卡且受每用户单活动任务限制，均不采用。
