# Verification: 两端首屏优先

2026-10-17，北京时间。

## Changes
- 共享portfolioSortQuery默认created_at请求membership模式，Web和Mobile统一；手机移除重复拼接。金额/盈亏等全库排序维持原算法；组合summary的旧getPortfolio显式full以保持连续页快照绑定兼容。
- Web首批页不带include_summary，先显示本页再独立加载账户汇总与纠错；搜索/排序/翻页复用统计，初次请求被搜索替换时仍会初始化统计。owner ref与请求版本在登出、鉴权失效和卸载时清理。
- 汇总等待时显示“—”，不显示未获取的零值。
- 照片录卡、平板扫描、成本面板与审计组件按需加载，局部ErrorBoundary防止组件加载失败替换整页；重试创建新dynamic/React.lazy实例。加载/错误提示置于可见固定提示层，不阻挡卡库。
- 工作期间已有快照过期“保留请求页码重建”的改动和相关测试予以保留，旧排序测试断言按当前行为更新。

## Evidence and TDD
- Web新断言先失败：原首屏仍合并include_summary且提前请求全库统计。共享参数新断言先失败：原构造器未设置membership。
- 审查发现搜索替换初次请求漏初始化统计；新增回归先失败，再修复。
- 生产Chrome拦截capture组件chunk，旧实现无法展示局部重试错误；新增局部错误边界后捕获成功，但重试按钮曾被侧栏遮挡，调整提示层后完整场景通过。
- 最终Web：`node --import tsx --test tests/dashboard-refresh.test.cjs tests/portfolio-sorting.test.cjs tests/purchase-cost.test.cjs tests/purchase-cost-background.test.cjs tests/purchase-cost-values.test.cjs`，93 passed。
- Mobile：`npm test && npm run build`，70 passed；TypeScript/Vite生产构建通过。
- API隔离PG：`bash scripts/test-mobile.sh apps/api/tests/test_portfolio_membership.py apps/api/tests/test_portfolio_membership_postgres.py apps/api/tests/test_portfolio_sorting.py apps/api/tests/test_portfolio_sorting_postgres.py apps/api/tests/test_portfolio_performance_postgres.py`，20 passed、3个可选性能基准skipped、1个现有Starlette/httpx弃用警告。
- Web工作目录tsc通过；隔离Next生产构建（含类型检查）通过。生产Chrome脚本 `apps/web/tests/portfolio-performance-browser.cjs` 1项复合场景通过：汇总一直等待仍显示卡片且可搜索/查看详情；首屏不下载capture/flatbed/cost对应chunk；capture chunk首次abort后卡库仍在，重试成功打开并关闭录卡；详情辅助接口失败不阻塞详情。最终无未捕获pageerror。
- 独立代码审查的两项P2均已修复，复审未发现其他明确P1/P2。
- git diff --check通过。

## Production Build Comparison
相同本机环境隔离构建，基线 `.runtime/first-paint-before/apps/web`，优化后 `.runtime/first-paint-after/apps/web`：

| Next构建指标 | 优化前 | 优化后 |
| --- | --- | --- |
| 首页路由Size | 69.1 kB | 54.1 kB |
| First Load JS | 178 kB | 163 kB |

这是构建报告体积，不是线上耗时或网络带宽实测。未声称线上提速百分比。

## Limits and Deployment
未部署到cardsp.cn、未打包/安装iPhone新版本；未使用真实登录账户或修改真实卡库。未运行后端全量测试、3个可选性能基准、真实iPhone冷启动和弱网性能测试。
手机版原有默认分页已采用membership，此轮主要统一共享规则并验证其不退化，不宣称手机获得未经测量的提速。金融排序仍需完整最终投影，全库汇总本身仍有成本；首屏列表已解除其依赖，未重写估值规则或统计算法。
当前云端已支持membership，无需新增数据库迁移；发布Web可使网页优化生效，手机共享代码需重建安装包。此次不部署上一任务的自动成本队列；该任务仍需单独0082迁移及API发布。

## 后续发布

同日已按用户要求发布网页版并覆盖安装iOS1.1.5/build9；上文“未部署/未安装”是实现阶段状态，最终交付见 [deployment.md](deployment.md)。生产镜像构建补齐了pnpm固定版本、workspace声明及构建期API_INTERNAL_URL；没有改变锁文件。
