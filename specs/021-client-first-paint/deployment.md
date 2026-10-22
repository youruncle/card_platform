# 首屏优先加载发布

2026-10-17，北京时间。

## 发布结果
- Web已发布到 https://cardsp.cn ，镜像 `card-platform-web:0.6.1-20261017-firstpaint1`。仅替换Web容器，API仍为 `card-platform-api:0.6.1-20261017-library1`，健康状态正常；数据库和worker配置未更改。
- iPhone 16 Plus已覆盖安装「我的卡库」1.1.5/build9；devicectl读回版本并成功启动，未卸载或清除应用数据。个人设备开发签名，未上传TestFlight/App Store。
- 发布内容为本轮分页、首屏统计解耦和组件按需加载，包含当前已有的网页快照失效后保留请求页码修复；上一任务自动成本队列尚未部署。

## 发布验证
- 发布前复跑Web相关测试93项、Mobile70项，全部通过；手机TypeScript/Vite、Capacitor同步、Xcode Release及codesign严格校验通过。
- 云端使用锁文件构建Linux镜像。构建过程中包管理器自动选择新版导致下载缓慢；固定pnpm9.15.9并补全workspace packages声明后安装/构建通过。Next主包最终自行下载完成，备用官方下载包未注入依赖。锁文件未变。
- Docker构建显式设置API_INTERNAL_URL=http://api:8000，检查构建后的routes-manifest两条代理均指向现有API；避免构建期把localhost固化。源码相关六文件SHA256与本地对应源码一致。
- 独立预览容器验证首页200、12个静态资源200、功能能力接口200、匿名auth/me401后才切换生产。预览容器已移除。
- 公网切换后重复验证同一组状态，全部符合预期。
- 公网真实JS/CSS与chunk的Chrome测试通过：API仅使用虚构账户/卡片边界响应，汇总一直等待时卡片可显示、搜索、查看详情；首屏不下载capture/flatbed/cost面板chunk；主动拦截capture chunk后只显示局部错误，重试能加载并关闭录卡；详情辅助接口失败不影响主体。无未捕获pageerror，不访问真实账户、不写真实卡库。
- 云端构建报告首页Size约54.2kB、First Load JS约162kB；本机前后对照178→163kB。体积指标不等于真实账户端到端提速比例。

## 产物与回退
- 云端发布目录：`/home/ubuntu/card-platform/staging/first-paint-20261017/`，含构建日志、源码校验清单与新镜像ID。
- 备份：`/home/ubuntu/card-platform/backups/first-paint-20261017/`，目录700，含原镜像标签、切换前Compose。原镜像额外保留 `card-platform-web:before-first-paint-20261017`。
- 回退时仅将当前Compose的web.image恢复为 `card-platform-web:0.6.1-20261016` 或上述备份标签，执行既有 `.env.cloud` 与 `infra/compose.tunnel.yaml` 的 `docker compose up -d --no-build --no-deps web`；保留当前API及其他配置，不恢复数据库或卷。
- 本地IPA：`.runtime/releases/20261017-first-paint/CardPlatform-1.1.5-build9.ipa`；SHA256 `a3b076a0684d4af99303728bc07f10dd4b9a36f84f9df6022eca1a122270b337`。
- 本地发布记录与模拟截图位于同目录；源码包、补充的Dockerfile和workspace配置由source.sha256共同标识。

## 未验证范围
未运行真实账户登录后的冷启动/弱网耗时对照、iPhone人工滚动及相机操作；公网模拟流程不代表这些实机验收。未重跑后端全量测试、上一阶段跳过的3项可选性能基准。API及数据库未发布新的业务逻辑。
