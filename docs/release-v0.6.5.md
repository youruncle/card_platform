# 0.6.5 / iPhone 1.1.16（20）发布记录

2026-10-23（Asia/Shanghai）。用户授权提交代码并发布网页、云端 API 和当前个人 iPhone 开发签名版。

## 本次源码

源码提交 `6b2b49f819ce8e73cfa40fc40122c5036d75e80c`，分支 `codex/cardhobby-valuation-quality`，已推送 origin。

- 提交知识录入SOP、封版/发布门禁、公开实卡挂牌限编证据、PDF绘制列恢复、离线媒体准备与组装工具，以及相关测试和既有规格记录。
- 发布前审查复现并修复：合法单面实卡因没有配对声明而被误拒绝；已封版配对丢失一面仍可通过最终门禁。单面从封存来源复核身份；新配对须恰好正反两面、不同字节、同源同身份同实物证据，统计也须一致。
- 保留已提交的行情筛选、DeepSeek视觉非思考、独立百炼qwen-plus搜索和成本worker改动。冻结时与实际运行API的214个Python/JSON资源核对，仅知识发布模块、API版本及新增PDF恢复模块不同。
- 并行开发中的知识采集worker、其桥接分支/测试/迁移和其他未提交历史材料保持本地，不纳入发布。发布镜像来自提交的冻结文件，未直接打包持续变化的工作区。
- 本次为应用源码发布，不再次导入知识内容，不更新本地运行知识库，不导入本地卡库、估值、订单、照片或采集材料。

## 发布前实测

- Web：191项测试通过，Next.js生产构建通过。
- Mobile：129项测试通过，TypeScript/Vite、Capacitor同步、Xcode Release及codesign严格校验通过。
- 最终冻结后端定向回归：264通过、1跳过（独立PostgreSQL用例）、1条上游Starlette/httpx弃用warning；相关Ruff与diff检查通过。两项门禁问题已留下预期RED与GREEN，并通过独立复审。
- 静态封存资料的门禁回放：20261022批126产品/275新增图和20261023-r2批15单元/33新增图均通过，未搜索/未审核均0。此项只验证已封存资料与门禁，不是重新搜索或新内容发布。
- 后端全套尝试达到10失败后停止：383通过、74跳过、10失败；以下10项在发布前HEAD `90631bc6`的独立副本全部复现。未宣称后端全套通过，未为发布任务修改这些估值/卡淘模块。

- `apps/api/tests/test_audit_batch02_admission.py::test_bound_target_back_cannot_be_ignored_by_front_only_marker_claims`
- `apps/api/tests/test_audit_batch02_admission.py::test_candidate_back_marker_claim_requires_an_uploaded_bound_back_asset`
- `apps/api/tests/test_audit_batch02_gallery.py::test_marker_back_claim_is_bound_to_actually_supplied_support_bytes`
- `apps/api/tests/test_audit_batch02_markers.py::test_target_printed_version_cannot_be_supplied_by_unseen_or_different_candidate[candidate0]`
- `apps/api/tests/test_audit_batch02_markers.py::test_target_printed_version_cannot_be_supplied_by_unseen_or_different_candidate[candidate1]`
- `apps/api/tests/test_audit_batch02_markers.py::test_target_printed_version_cannot_be_supplied_by_unseen_or_different_candidate[candidate2]`
- `apps/api/tests/test_audit_batch02_markers.py::test_missing_marker_observations_cannot_claim_same`
- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[payload0-None-empty]`
- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[payload1-None-succeeded]`
- `apps/api/tests/test_cardhobby_audit_trace.py::test_page_and_every_rejected_row_are_traceable[None-error2-failed]`

## 云端部署与回读

- API/Web均运行 `0.6.5-20261023-release1`，镜像revision与源码提交一致；实际API版本0.6.5，ready通过。
- 运行容器216个API资源在已安装包和/app/src双路径逐一核SHA，通过；未包含后续采集worker桥接依赖。
- 成本worker的实际环境与应用设置均true；行情worker=true、图片自动调用独立日限100保留。四个视觉角色均deepseek-flash且显式thinking disabled；搜索为独立原百炼端点/密钥与两个qwen-plus模型。
- `.env.cloud` SHA未变；运行容器环境哈希、全部挂载与切换前一致。Compose只替换api/web镜像，未迁移数据库或重导知识内容。
- 云端知识库仍v23/revision1853，checksum `78e1bf3a8400530c264b3ccc5a1f376160debb1d785ac7db75e1d69ce5a730fc`，30,227个快照实体及发布指针与切换前一致。
- 新API镜像已挂载实际云端资料并仅作读取，重放20261022和20261023-r2两批封存门禁，通过；未写入知识或业务数据。
- 公网 `https://cardsp.cn/`、`/knowledge`、`/knowledge/manage`、`/player-prices`均200；分别12/12/11/11项关联静态资源请求均200并保存SHA。匿名知识、卡库、账户接口均401；配对及扫描能力接口通过。未使用用户密码或创建验证会话。
- API镜像ID：`sha256:43a80266cca1d3bef18ed9bf9a94ceb1e5182cadb074d182af3a28d408e17c41`。
- Web镜像ID：`sha256:0f73067f3574f06329c761378ff64fa4a885ffc680de959342108a96c22956b1`。

云端备份在 `/home/ubuntu/card-platform/backups/release-20261023-v065/`，包含455,743,328字节的完整数据库dump（pg_restore目录验证通过）、原Compose、原镜像名/ID、环境哈希和0600签名IPA。数据库SHA256 `ef220be984fd6c73dba84a8a867c8182275ec8d4ab1325a9cb80402e27cb3f7f`。镜像切换失败会仅恢复原api/web镜像并验证成本worker仍true；本次未触发回滚。

本地构建、冻结源码清单、测试/基线失败日志、设备状态与部署收据位于 `.runtime/releases/20261023-v065/`；云端staging同名目录保留构建和激活脚本。此前其他任务的未提交改动和样本保留。

## iPhone构建与安装验证

沿用个人开发团队 `YLV27B9G9U`、bundle `cn.cardsp.alpha.local`；签名描述文件有效至北京时间2026-10-29 09:53:01，包含原iPhone 16 Plus。安装包：`.runtime/releases/20261023-v065/CardPlatform-1.1.16-build20.ipa`。

IPA SHA256：`1ef7eaac7a861981d062ae64d302c1bb02ecdf23bef9995d854c32a7b3f879b1`。

2026-10-23初次发布时设备不可连接，安装暂缓。2026-10-27用户连接并解锁后，设备安装前已读到1.1.16/build20；本次仍使用上述SHA一致的原签名IPA，解包后重新通过codesign严格校验，再沿用原bundle覆盖安装成功。安装后设备再次读回1.1.16/build20，启动PID86576，随后独立进程查询确认仍在运行。未卸载或清除应用数据，未逐项审计应用内部数据；本次不包含真机登录、相机及页面交互验收。

本次复查云端API仍为0.6.5且ready通过，运行成本worker=true、四视觉角色deepseek-flash显式thinking disabled、独立原百炼qwen-plus搜索配置均通过。安装、版本、启动、进程及云端复查收据位于 `.runtime/releases/20261023-v065/install-20261027/`。
