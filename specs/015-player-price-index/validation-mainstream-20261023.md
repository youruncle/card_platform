# 主流参考卡选择纠偏验证（2026-10-23）

用户指出库里参考卡过于便宜，至少应选择Topps正系列折射或带编。沿用本功能唯一spec/plan/tasks的FR-020/021及T035–T037，保留既有知识库任务的未提交修改。

## 真实复现与修复

云端只读报告日2026-10-22：库里Prizm #134普通Base六笔，两窗各三笔，中位价1→3 CNY，错误地成为+200%球员参考卡；Silver Prizm仅一笔52.10 CNY，前期零笔。公开成交原事实没有改写。原捕获`cloud-before.json`保存在本次私有证据目录。

集中筛选正系列折射/明确限编，产品为Topps／Topps Chrome、Prizm／Donruss Optic／Select，拒绝厂牌矛盾、未知/否定版本、具名特卡及签字/物料/兑换。普通Base不能进入参考卡或篮子；纸卡Silver Foil、Chrome原生材质、裸Prizm词不冒充折射。仍按精确同款/总编/评级分组，每个30日窗口至少三笔，合格卡内部再按既有成交活跃度选择。主流候选在明细优先，其他卡保留自己的成交与价格变化。已持久化旧篮子/快照不能绕过新规则；GET不写业务表，旧合格月篮子仍固定。

## 回归与审查

- 新选择测试先27失败/1通过，修复后通过；追加四个原生/模糊版本反例先4失败，独立审查再指出unknown/non修饰穿透，追加四个反例实测4失败/32通过后修复，并覆盖中文未知/裸Prizm。主流候选排在Base后、旧持久快照保留指数均先真实失败再修复。原始失败输出保留，没有把预期失败计为验证通过。
- 新建隔离空PostgreSQL数据库，执行全部87个迁移后，行情后端最终 **500 passed**，包含真实持久化/账户隔离/旧月篮子；1个既有Starlette/httpx弃用警告。命令：`TEST_DATABASE_URL='host=127.0.0.1 user=song dbname=card_platform_selection_20261023' PYTHONPATH=apps/api/src .venv/bin/pytest -q apps/api/tests/test_player_price*.py apps/api/tests/test_player_market.py`。
- Web **194 passed**；Mobile **129 passed**；Web typecheck、云端Next生产构建、Mobile TypeScript/Vite及Capacitor同步、Xcode真实Release构建、codesign deep/strict、Ruff、diff check均通过。未运行全部API历史套件，不宣称全量API回归通过。
- 独立只读review指出1项Important（未知/否定版本），修复复核关闭；最终排序及跨20卡选择复审无新增重要问题。复审分别49/42项聚焦通过。

## 云端与真机

API与Web实际切为`card-platform-{api,web}:0.6.4-20261023-selection1`。API以当时运行prices1镜像为基底，仅覆盖domain/repository两模块的site-packages及src副本；发布前逐SHA确认旧两模块与当前基线一致。Web原88源文件逐SHA对照前次537fcc58冻结包，再叠本次改动，未混入知识库未提交文件。最终源包90文件，SHA256 `f760be531624d20ed848098a42f65a44f14f3090a492db68b2feef9f9f78a0ec`。

运行API内实际校验两个模块SHA、健康、成本worker=true、行情worker=true、核图日限100、DeepSeek全视觉deepseek-flash且显式thinking disabled、独立百炼qwen-plus/独立密钥。原.env.cloud逐字节保持，未上传本机业务数据或同步本地知识库。首页、行情、知识库、管理页及各自静态资源通过；匿名私有接口401。

云端真实读回2026-10-20/21/22三个报告日：305主体、9位有核验成交、**0位符合新口径的主流参考卡趋势**、0三卡指数。此前五位Base趋势是旧口径的历史验收，不能继续冒充新口径达标。库里保留7笔/2款，reference_card=null、reference_status=insufficient_mainstream_sales，Silver在明细首位；Base自己的+200%仍作单卡事实保留。现有一条采集异常保持可见，未借本次选择纠偏清错或伪造日更。

公网Chrome真实临时验证会话检查Cookie/Bearer、搜索、排序控件、库里首屏无+200%或Base参考卡、银折和原始成交证据、390px零横向溢出、零pageerror、匿名401、空账户隔离及跨账户404。两次验证共四个10分钟会话均立即撤销，临时空账户删除，撤销Bearer均实测401，秘密文件删除。首次浏览器检查因Playwright缓存浏览器缺失未启动，改用已安装Chrome后真实通过，原失败日志保留。

个人iPhone覆盖安装`cn.cardsp.alpha.local` **1.1.15/build19**，实际读回版本，启动PID64774且随后进程仍在；未卸载或清空应用数据。首次Xcode拉取GitHub依赖遇HTTP2失败，停止该构建后改用上次已验证Swift包缓存、禁止自动解析/更新，Release构建成功。包内验证主流卡新文案；未逐项手工重演真机所有屏幕交互。

## 备份、回退及证据

服务器私有目录`/home/ubuntu/card-platform/backups/player-selection-20261023/`保留发布前Compose、环境副本、原镜像名及423094047字节完整pg_dump，pg_restore目录验证通过。无新增迁移，未改源成交或私有卡库。回退仅将当前Compose的api/web镜像分别改回`card-platform-{api,web}:0.6.4-20261022-prices1`，再使用原`.env.cloud`执行`docker compose --env-file .env.cloud -f infra/compose.tunnel.yaml up -d --no-build --no-deps api web`；保留成本worker=true及视觉/搜索配置，不恢复历史关闭配置或业务数据库。

首次云端构建因legacy Docker不支持COPY --chmod失败，改普通COPY加chmod后重建成功；未因此切换失败镜像。最终逐文件manifest、原始失败/成功日志、三日云端回读、浏览器截图/证明、会话清理及真机证明在`.runtime/player-price-selection-20261023/`，筛选修复不表示已补足真实主流卡样本或证明未来价格稳定。此任务完成的是选择纠偏及双端发布；主流卡样本缺口按真实数据保留。
