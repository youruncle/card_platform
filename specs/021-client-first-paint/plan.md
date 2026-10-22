# Implementation Plan: 首屏优先加载

## Technical Context
Next.js15/React19、Capacitor/React、共享TypeScript card-core、FastAPI/PostgreSQL。

## Constitution Check
不改业务/估值规则，不移除鉴权和快照所有权校验，保留未提交代码；隔离验证不读取真实账户。

## Design
共享portfolioSortQuery在默认created_at且未显式要求full时自动带snapshot_mode=membership，Web/Mobile沿用同一构造器；兼容旧getPortfolio需要合并summary的调用时显式full。网页refresh拆掉combineSummary，只传列表参数；列表成功设置卡片状态后触发refreshMetrics独立请求，首个有效页即使是搜索替换请求也初始化统计；以owner ref避免重复，保留版本检查、搜索和刷新规则。Mobile移除重复membership拼接，增强首批/追加测试。
网页通过next/dynamic按需加载CapturePanel和FlatbedBatchPanel及成本弹层（同模块的Audit亦按需，避免静态引用回拉整包），可见loading占位及面板局部错误边界；retry重建React.lazy实例，保持服务端静态页框架。若测试揭示加载时序问题按异步可见状态验收。
后端现有membership SQL已支持，只增加两端契约集成验证，无需改动查询语义或数据库迁移。

## Validation
先修改断言观察旧实现失败，再实现。Web dashboard及成本相关测试；Mobile完整测试及build；隔离PG membership/sorting/performance/mobile回归。隔离生产构建与Chrome模拟登录API测试，悬挂summary证明列表优先；保留页面错误及按需加载检查。生产代码不部署。以相同构建的manifest/输出比较首屏体积，不把模拟测试当线上耗时。
