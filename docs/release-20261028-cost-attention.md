# 2026-10-28 自动成本卡淘验证提醒发布

用户授权将本次改动发布网页、云端API和当前个人iPhone开发签名版。

## 源码与验证

- 源码提交 `daeb7c1e4db146528cc0eb27a643169beed2ca67`，已推送原分支。冻结HEAD后仅叠加本次21个文件；其他任务未提交的MCP、知识采集、识别修复等内容均未纳入。
- 发布前核对线上215个API Python/JSON资源与HEAD一致；新发布仅3个API模块变化，包含账号阻塞汇总、成功验证后受限恢复任务。网页/手机共用前台轮询、自动弹窗与关闭去重机制，复用原卡淘表单。
- 冻结产物定向回归：API246、Web196、Mobile135项通过（合计577）；API仅1个现有Starlette/httpx弃用warning。网页Next.js及手机TypeScript/Vite、Capacitor、Xcode Release和严格codesign检查均通过。未重跑整个API测试集。
- 个人团队 `YLV27B9G9U`，bundle `cn.cardsp.alpha.local`，手机1.1.17（21）。现有签名描述文件有效至北京时间2026-10-29 09:53:01。
- 已覆盖安装到原iPhone 16 Plus，设备应用列表读回1.1.17/build21；启动PID94667，独立进程列表复核仍在运行。未卸载或清除应用数据，未逐项审计内部数据，未代用户完成真实卡淘验证码。
- IPA SHA256 `5a577da09d25e9e71945719f88e410fecf52d85e8070923bc778e1722635f69c`。

## 云端切换与回读

云端数据库备份580,992,547字节，pg_restore目录检查通过，SHA256 `59d6761db8f94309c4c3e8f9505b17503a551cb160ffa030d7011a15daaba292`。Compose、原镜像ID、环境哈希和签名IPA已备份至 `/home/ubuntu/card-platform/backups/release-20261028-cost-attention/`，目录0700、文件0600。

API/Web目标镜像 `0.6.5-20261028-costattention1`。本次无需数据库迁移，不重导知识、卡片、订单或估值；环境及数据挂载保持，成本worker必须为true。激活脚本仅替换API/Web镜像，失败时仅恢复旧镜像并验证worker仍为true。

本地冻结源码、构建日志、测试、设备及部署回执位于 `.runtime/releases/20261028-cost-attention/`。切换与回读均已完成：

- API/Web实际镜像均 `0.6.5-20261028-costattention1`，revision与源码提交一致，API healthy、ready通过。API镜像ID `sha256:84eb7ba04740e77efbaf624fc0d6ba149ff9ca81c0ff26ccf2a97e02385c407f`，Web `sha256:eae99e76024342abd473df0acb4fdacc72062f4bcce0a35f2eed5cf42fba2477`。
- 215个Python/JSON源码资源与冻结清单一致；连同生成缓存等共406个API文件在安装包及/app/src两路径逐一核SHA。环境哈希、全部挂载及.env.cloud SHA与切换前一致。
- 运行容器成本worker环境与应用设置均true；四视觉角色deepseek-flash、显式thinking disabled，独立原百炼qwen-plus及搜索密钥隔离均通过。知识库仍v23/revision1853，发布ID及checksum与切换前一致。
- 公网 `/`、`/knowledge`、`/knowledge/manage`、`/player-prices`均200，分别12/12/11/11个关联静态资源全200且留SHA。新增自动成本汇总接口及其他私有接口匿名均401。
- 使用原已登录Chrome真实会话刷新首页，未点击逐卡获取即主动弹出“自动获取成本需要验证卡淘”，显示26张受阻卡；关闭后提醒、重新打开均成功。旧验证会话过期，“重新发起验证”入口可见；弹窗已留给用户处理。未发送短信、代登或完成验证码；成功验证后的实际订单召回与成本写入仍待用户验证后观察。
- 发布检查脚本首轮使用app.routes逐项读取path，不兼容当前FastAPI包含的_IncludedRouter；改为OpenAPI路径检查后镜像检查通过。此问题仅在部署检查脚本，首次失败时生产未切换。OpenAPI生成有既有重复operation ID warning。最终激活通过，未触发回滚。
