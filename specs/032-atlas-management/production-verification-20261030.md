# 图鉴管理首版网页与API：生产验收回执

2026-10-30，依据用户“可以上线”及后续“已允许”的明确授权执行。范围仅网页＋API；无原生iPhone发布、无schema迁移、无生产凭据/角色扩张、无合并拆分、无收藏/实卡/账本写入，未push/merge。执行工具均已允许并成功返回，没有待处理审批、未决结果或权限拒绝。

## 现场基线与操作

本轮16:32 UTC首次刷新实际现场时，目标管理版本已在运行，API创建时间04:46:22Z、web04:46:43Z，当前Compose来自既有 `20261030-121833-hourly-atlas` 发布目录。不是历史只读镜像。为遵守不重复被批准操作/不创建新发布的要求，本轮没有重复构建、切换、回退或重启生产服务，而是核验现有构建/预热/契约记录，补齐目标机真实auth隔离写流程及生产只读浏览器验收。新增远端目录仅保存5个隔离测试源码/既有测试DDL文件，不是生产发布或证据包。

当前已验证可直接访问：[图鉴管理](https://cardsp.cn/knowledge/manage)、[既有发布管理](https://cardsp.cn/knowledge/manage/legacy)、[知识浏览](https://cardsp.cn/knowledge)。三者公网HTTP200。浏览器使用用户已有Chrome登录会话，没有提取cookie/token或新建生产账号。

| 项目 | 实际运行版本 |
| --- | --- |
| API | `card-platform-api:atlas-manager-20261030-dfcd0da5` |
| API完整digest | `sha256:890f5f22d7bed8ed9916f13fb8d8889c56d81cc4000a49cd7a46bfb357cbe2ff` |
| 兼容恢复 | `card-platform-api:atlas-compatible-recovery-20261030-dfcd0da5`，与候选相同digest的兼容内核 |
| web | `card-platform-web:atlas-manager-20261030-dfcd0da5` |
| web完整digest | `sha256:c53d9ecea9817bc908a208b9bd6a99bcd2c0055606f462d519096422c8418748` |
| web BUILD_ID | `tZ1cNlv2XQqYu3ATXhip-` |
| 知识 | v27 / rev1861，`0710ef3f-c147-45bc-9619-9d8c83a6887f` |
| 知识checksum | `87a77700583d48bad400a9236cdfa57637c7bdfb345b34acadf1bf698622690e` |

六个运行管理模块hash与本地最终提交 `dfcd0da5055d054ea7b50c1244c72a49e250e02f` 一致。与实际上线前只读父镜像119b逐文件比对：仅knowledge_api/browser/repository三个修改，atlas_api/corrections/lifecycle三个新增，删除0；其余Python模块保持。实际代理仍保留/mcp、/.well-known、/v1、/uploads到api:8000。生产当前环境hash、挂载、运行用户、network及镜像在本轮前后均相同，未整体部署旧worktree。

## 目标机隔离验收

在独立internal Docker网络新建一次性PostgreSQL，不挂生产卷、不复制生产凭据、不开放端口；候选容器只挂只读测试源码。数据库及测试角色仅在该临时容器，既有生产用户/权限没有变化。候选直接执行此前已验证的原测试函数：镜像没有pytest，用薄装饰器/异常上下文适配，不替换current_user，实际cookie/Bearer/hash/过期/停用/撤销/知识角色数据库查询均运行。

**15个目标验收场景通过**，总15.485秒：viewer/editor/reviewer/无知识角色伪造publisher/admin拒绝；缺会话、过期、撤销、disabled及全局非admin拒绝；editor提案/reviewer审核/publisher发布回滚/admin恢复分离；被拒绝请求无知识写入；bootstrap真实身份；预览无写入；稳定幂等键/内容绑定；并发提交与审批冲突；拒绝与撤销；版本冲突；显式发布/知识版本回滚/恢复时间线；自动发布保留已发布覆盖且不消费新人工草稿。

合成长历史为2万无关审计、2千提案、500后续版本。目标测试容器限制768MB/0.75CPU，100条页约2912ms、单图鉴501版本历史约2253ms。不是无条件生产延迟SLO。

随后从兼容恢复tag启动**另一个容器/新进程**，重读同一隔离DB中的3个提案及已发布纠错：pending/approved未发布状态保留、有效总编25保持、再次自动发布继续携带、viewer冒充拒绝。恢复tag和候选digest相同；这是相同安全兼容内核的新进程恢复，不声称旧119b镜像支持纠错。已有旧网页协议再次发布也已在目标场景执行；没有重新切换真实生产网页来测试。

两测试容器均exit0、无OOM；一次性PostgreSQL容器/匿名卷/internal network已全部删除。原测试函数仅替换合成来源快照组装器，未替换会话授权。该限制与执行方式在证据中明确，不冒充目标pytest32项结果。

## 全量预热、旧客户端及资源

核验现有目标机baseline/contract/prewarm容器均exit0、无OOM，均绑定当前v27知识id/checksum。预热完整2,169,016来源、565产品、831图片、14来源配对，issues=[]。冷建649.023秒、温开10.610秒、峰值RSS412,549,120字节；当前派生缓存4,535,574,528字节，共存缓存25,931,939,840字节。现场剩余磁盘233GB、可用内存约1.67GB，可容纳已记录负载；未删除既有缓存。

比较35个旧目录响应：已有字段修改/删除0；六个卡片详情仅追加pair_binding_status。这是旧字段兼容，不声称JSON逐字节零差异。此只读契约harness的auth override不计入真实角色测试。跨2023/2024/2025样本、分页、精确球员/卡号、错误参数及图像响应有现有收据；本轮未重新耗时冷建相同快照。

## 生产只读验收

真实已有管理员Chrome会话GET权限返回admin/can_propose/can_review。正式HTTPS域名页面显示v27：来源2,169,016、确认702,430、待确认1,466,586、冲突0、双面14。按Hansen Yang＋卡号14查询，跨产品110条来源记录；打开Court Kings Masterpiece版本，身份字段、原始来源链接、缺图骨干与纠错入口可读，未创建/审批/发布任何生产提案。

临时400px响应式视口下详情独占显示、关闭保留球员/卡号筛选，浏览器返回恢复同一card_id；已关闭DevTools、退出移动模拟并关闭本轮新标签，保留原用户标签与未提交扫描流程。截图见同目录证据。首次页面加载曾持续等待，随后完整返回；本轮未对生产真实管理员的冷首开/网络延迟作正式SLO承诺。内置浏览器两次超时后改用现有Chrome会话，没有绕过认证。

公网管理/legacy/浏览页HTTP200；无会话读取权限和空体POST提案/发布均401，actor头不能代替登录。API内部HTTP /health/ready=200/status ready。合法生产写入及真实viewer/editor/reviewer账户不在本次生产样本授权内，正向写流程与角色矩阵在目标隔离库完成。

16:50:48Z最终只读核查知识仍v27/rev1861，审计795条、atlas审计0条，与本轮开始相同；没有生产知识/业务样本或数据回退。实际登录GET按既有机制更新auth_session.last_seen_at，这不表述为整个数据库零写入。成本worker实际true；视觉deepseek-flash/thinking.disabled，独立百炼qwen-plus搜索及不同密钥关系保持（不输出密钥）。

## 交付与边界

独立复核 `/root/atlas_security_review` 对隔离连接、15场景、跨进程恢复、清理收据和生产前后状态进行了只读复核，无具体安全阻断。核心首版可正常访问，未加入合并/拆分，持久化复用审计JSONB与不可变快照，不需要数据库迁移。

兼容恢复只能使用已验证的安全内核；网页可以独立退回，但不能将不识别atlas_corrections的旧119b API作为普通恢复目标。没有生产正向纠错样本测试；这是明确范围限制，不以改用户数据弥补。证据只保存在本机，没有上传证据包或push/merge无关代码。

支持证据：`verification/production-20261030/`；原始远端只读收据及执行脚本在本机 `.runtime/atlas-release-20261030/`，未复制环境文件/凭据。
