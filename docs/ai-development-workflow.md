# Spec Kit 与 Superpowers 配合使用

## 安装

Spec Kit CLI 1.0.8 安装自 github/spec-kit 的 v1.0.8，commit 0cc9a6a1159471a3108b9bad718ba17006dd6039。
可执行命令为 `specify`，独立 Python 环境位于 `~/.local/share/spec-kit/venv`。
本项目使用 Codex skills 集成：`.agents/skills/speckit-*`；共享模板和脚本在 `.specify/`。
已包含官方 bug 扩展。Superpowers 通过 Codex 插件目录单独安装；本文件不代表插件已安装。

## 功能开发

按需要调用 `$speckit-specify`、`$speckit-clarify`、`$speckit-plan`、`$speckit-tasks`、`$speckit-analyze`。
以同一功能的 spec.md、plan.md、tasks.md 为依据执行实现；Superpowers 用于实现、测试、调试和审查，不复制一套计划。
完成后用 `$speckit-converge` 核对验收项。以现有代码为上下文，只为本次变更写规格，不追溯重写整个系统。

## 识别缺陷

在项目任务中可直接说：

> 使用 Spec Kit 和 Superpowers 排查这次卡片识别问题。先检查已有失败样本，按裁切、方向、正反面匹配、OCR、身份字段提取和联网核验逐段定位。记录预期与实际结果、根因证据，再做最小修复并回放失败样本及正常对照样本。共用一份任务记录，报告实际验证结果。

若分阶段执行，使用同一个明确的 slug：

1. `$speckit-bug-assess <具体错误、样本或卡片编号> slug=<问题短名>`
2. `$speckit-bug-fix slug=<问题短名>`
3. `$speckit-bug-test slug=<问题短名>`

记录位于 `.specify/bugs/<问题短名>/`。批量问题先归因、按影响排序，再逐类修复；不依据一张卡的成功宣称整体准确率提升。

## 验证

- 在项目根目录运行 `specify version`、`specify integration list`、`specify extension list` 检查安装。
- 根据改动运行相关单测、样本回放或接口测试；界面改动验证实际用户路径。
- 比较相同样本集上的修复前后结果；明确样本量、失败类型、尚未覆盖的情况。只有实测时才报告准确率、耗时或调用成本的变化。
- 不能访问外部模型或没有真实样本时，说明验证范围，保留未完成项。
- 现有 Codex worktree 是独立目录；需要时在目标 worktree 另行接入这些项目文件。


## 发布约定（2026-10-18）

用户说“部署到线上”默认同时包含网页端和手机端，以及本次功能所需API。手机沿用当前个人iPhone开发签名渠道，覆盖安装后验证版本与启动；保留应用数据。

本机已配置SSH别名`card-platform-deploy`，连接现有云服务器ubuntu账户，使用独立Ed25519部署密钥及30分钟连接复用。私钥仅保存在本机`~/.ssh/card_platform_deploy`（0600），不写进仓库、发布包或日志。服务器追加受限公钥（禁用PTY和转发），保留原公钥及扫码登录策略；独立新连接已验证publickey认证。服务端仍可能输出二维码横幅，但密钥认证会直接完成，无需扫码。后续部署优先用`ssh card-platform-deploy`与`scp`该别名；不要反复发起扫码登录，也不要复制整份本机.env/业务数据或覆盖远端Compose配置。


### 自动成本 worker 持续开启（2026-10-19）

用户明确要求以后部署持续开启自动成本 worker。生产 Compose 使用 `PURCHASE_COST_WORKER_ENABLED: 'true'`；应用默认值也为 true。发布、重启和回滚都必须保留此设置，不能恢复旧备份中的 false。仅用户明确要求暂停时才关闭；测试隔离和故障处置文档中的关闭示例不代表生产默认。

发布前检查目标 Compose，发布后在运行 API 容器内读取 `get_settings().purchase_cost_worker_enabled` 并断言为 true，同时确认 API 健康。历史 `.runtime` 发布脚本或验证脚本可能包含 false 断言，不得直接复用；先按此约定更新。回滚应只恢复镜像版本，保留 worker 开关为 true。
