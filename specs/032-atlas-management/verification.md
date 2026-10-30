# 图鉴管理首版本地实现与验证（2026-10-30）

## 交付与边界

独立工作树 `/Users/song/Documents/Codex/2026-10-30/task/card-platform-atlas`，分支 `codex/atlas-management-20261030`，基线 `64cf55d17e63059fae62e6dba0fc1c9701ee643a`。原仓库未提交内容及另一个 API 发布工作树没有写入；没有部署、push、merge、生产数据库写入、生产 schema 迁移、收藏/账本/实卡变更。新页面没有内置演示数据。

- `/knowledge/manage` 现在是图鉴表格与详情面板；年份、制造商、品牌、产品、子集、精确球员/卡号、身份/图像状态筛选，分页及URL历史保留。
- 详情呈现身份、实际正反图、字段级来源边界、来源记录/图片证据及冲突、相关版本、提案与审核记录。未知不猜测；来源内ID不冒认全局canonical。双面、同来源两面配对、审核身份绑定分别呈现。
- 纠错先预览旧/新/影响；理由和HTTP(S)证据必填。会话用户与 knowledge_member 角色由服务端校验，不信任可伪造actor header。
- 提案及审核复用现有审计日志；共享发布锁、内容绑定幂等键、来源hash/身份指纹和发布水位检查。取消/返回不写；网络重试同请求键。
- 批准只成草稿；显式人工发布将覆盖纳入不可变快照，回滚恢复对应覆盖集。自动官方来源发布只保留已发布覆盖，不带出人工草稿。已审未发布草稿可经审核驳回；已发布历史不可改写。修改身份后原图片绑定在派生目录解除，避免旧实卡照片证明新身份。
- 旧词典/批次/发布/回滚页完整保留在 `/knowledge/manage/legacy`，SHA256 `b3c770fc2f55201a41659f60b548ef217630222e46d36545043eefc97ae5f05f`，与原文件逐字节一致。

## 显式依赖

本任务基线未包含另一任务的读取适配。复制该任务的 `knowledge_atlas.py`、`knowledge_browser.py`、`knowledge_browser_api.py` 及其测试到本工作树后独立扩展；初始逐文件hash见 `readonly-dependency.json`。读取报告 `atlas-browser-integration-20261029.md` 和 `atlas-readonly-release-readiness-20261029.md` 已参考，没有重新执行它们的生产动作。最终差异包含这项依赖；后续集成应在当时实际已发布读取版本上核对差异，不整体覆盖其他任务更新。

## 实测

最终相关 Python 回归 **155 passed**（5.65秒），1个既有Starlette/httpx弃用警告。命令：

```sh
ATLAS_TEST_DATABASE_URL='postgresql://atlas_test@/postgres?host=/tmp/card-atlas-pg-socket&port=55569' PYTHONPATH=apps/api/src /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python -m pytest apps/api/tests/test_knowledge_atlas_corrections.py apps/api/tests/test_knowledge_atlas_api.py apps/api/tests/test_knowledge_atlas_browser.py apps/api/tests/test_knowledge_browser.py apps/api/tests/test_knowledge_browser_api.py apps/api/tests/test_knowledge_card_catalog.py apps/api/tests/test_knowledge_release.py apps/api/tests/test_knowledge_publication.py apps/api/tests/test_knowledge.py apps/api/tests/test_knowledge_media.py -q
```

这包含真实隔离PostgreSQL事务、同键并发提交、竞争审批、会话角色/actor伪造、幂等内容冲突、批准/发布/回滚、回滚后不复活、过期草稿撤销替换、自动来源发布隔离、来源hash漂移、图像配对与身份绑定冲突。PostgreSQL由本任务在 `/tmp/card-atlas-pg-20261030` 创建，socket `/tmp/card-atlas-pg-socket` / port55569；每个测试独立schema，结束删除。未读取应用生产DSN。

网页验证曾只读复用原工作区的 `apps/web/node_modules`（临时symlink），没有安装新依赖；交付前已移除该临时链接。复跑可自行安装同锁定依赖或重新建立同一只读依赖链接。

网页相关 **25 passed**：在 `apps/web` 运行 `node --import tsx --test tests/knowledge-atlas-manager.test.cjs tests/knowledge-browser.test.cjs tests/official-checklist-progress.test.cjs`。含预览/取消、失败重试请求键、URL返回筛选、只读、异步年份选中值、来源冲突、未发布批准可驳回、发布历史不可驳回。

`tsc --noEmit` 通过；新Python模块和测试的 `ruff check` 通过；`git diff --check` 通过。完整 `next build` 通过（两个管理路由生成成功）。仓库原 `next lint` 没有ESLint配置，会进入配置选择；本任务未安装或改造lint依赖，不能声称独立ESLint已通过。没有重跑已知大量无关全仓失败/15万参数化用例，不声称全仓全绿。

## 真实浏览器 QA

通过 CUA/IAB 实际 Chromium 渲染和操作（非仅jsdom）。本机QA服务只使用历史冻结v26的一份真实Prizm产品快照：16489来源记录；Hansen Yang + #14返回71明确来源版本；双面筛选1条真实eBay正反图，和官方骨干同一来源内ID。此为有界历史样本，非当前全市场覆盖或线上数据重读。

已验收桌面/390×844手机、精确筛选、来源证据、实际图片、关闭/浏览器返回、预览取消无记录、提交/批准仍保留发布值25、批准后驳回未发布草稿、只读成员、空列表、422错误与清除筛选恢复；手机document.scrollWidth=390，无页面横向溢出。QA中的25→26提案明确标记“仅本地QA”，只在隔离库中，未发布。

截图在 `verification/`：`desktop-final.jpg`、`mobile-final.jpg`、`desktop-real-v26.jpg`、`mobile-detail.jpg`、`mobile-list.jpg`、`proposal-preview.jpg`、`approved-draft.jpg`、`withdrawn-final.jpg`、`mobile-readonly.jpg`、`empty-state.jpg`、`error-state.jpg`、`recovered-list.jpg`。详细收据见 `browser-results.json`。

试验错误如实保留：初始沙箱不允许PostgreSQL共享内存/连接，获本机执行权限后完成；初选端口被占用，改用任务独立55569/socket。Standalone Playwright附带Chromium未安装；改用Chrome后脚本假设的按钮标签不匹配真实数据而超时，随后全部浏览器用例通过IAB按实际页面完成。初始构建DNS无法获取既有Google字体，允许网络后成功。无新增软件安装、无权限拒绝待处理、无未知写入结果。

## 一次独立审核与修复

独立审核报告 `verification/independent-review.md`：无Critical，4项Important。均写失败回归后修复：

1. 回滚后已审草稿阻塞发布 → 新增对未发布批准的审计驳回；列表/竞争/发布均读取最新审核事件；已应用历史不可撤销。
2. 自动来源发布带出人工草稿 → 自动发布只携带既有发布覆盖，不消费新草稿。
3. 指纹未绑定来源证据 → 包含source_key/source_checksum/source_variant_label/official_artifact_sha256，源字节改变隔离旧覆盖；源码指纹包含纠错模块，旧派生缓存自动失效。
4. 冲突图片显示已审配对且来源隐藏 → 分开source-face配对与pair_binding_status；新界面显示来源映射/声明/冲突，有待审映射即撤下肯定审核配对文案。

修复后92项相关核心回归及23项网页回归通过，随后扩大到最终155/25。最终实际浏览器再验来源面板和批准→驳回流程。按要求未启动第二轮独立审核。

## 仍保留的Minor与上线评审要求

- 提案列表已区分未发布批准与“曾进入发布历史”，但尚未显示具体应用版本/当前是否被回滚；完整应用版本时间线后续再补。
- `atlas.serial_status` / `autograph_status` 仍沿用原始来源派生说明；有效纠错值及字段证据在卡字段/field_sources。清空总编时元数据可能仍显示原来源known。新界面按实际字段显示“未知”，未把该元数据冒充当前有效值；后续应明确原始与有效元数据或重算。

**可以进入单独上线评审，不能由本报告直接上线。** 当前代码只做单产品真实浏览器验收，新的派生索引增加状态表且缓存版本/源码指纹变化；上线前必须在最新实际云端代码基线重对齐依赖、对完整快照重测资源/冷建/预热、验证真实会话角色与发布回滚演练。另一任务的API上线许可不覆盖本界面；不应整体部署此旧基线工作树。无生产schema迁移需求。
