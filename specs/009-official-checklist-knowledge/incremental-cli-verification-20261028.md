# 2026-10-28 Mac 只读增量 CLI 接入记录

## 仓库与补丁接入

- 完整原仓库：`/Users/song/Documents/Codex/2026-07-19/w/card_platform`。
- 远端：`git@github.com:youruncle/card_platform.git`；原分支 `codex/cardhobby-valuation-quality`，原 HEAD `bf527af8e90930a059cbbc5eb6c87ec7015aa86c`。
- 独立工作树：`/Users/song/Documents/Codex/2026-10-28/task/card-platform-incremental`；分支 `codex/knowledge-readonly-incremental-20261028`。
- 原工作区有既有未提交修改，未复制、覆盖或提交这些修改；读取了 AGENTS.md、constitution、开发流程、知识录入 SOP 及相关 speckit-implement 技能。
- Library 文件 `libfile_0c1ad16e8cb08191881e7f7ae73c9586` 版本 0 已通过当前 Library helper 在本机物化。ZIP SHA256：`4f7908d3ebcc42248527adafb8721f809bd6705840ff5750860973229603e3a2`；包内 48 个清单哈希全部匹配。包内无 README，以 `DELIVERY.md` 为交付入口。
- 补丁基线 `635c28433590dd3e56511c4072722a5c521a32e2` 是当前 HEAD 的祖先。检查当前提交及原工作区未提交模块，未发现本 CLI 合同或实现；既有 intake worker 是图片采集/审核流程，不是本只读规划器。
- `git apply --check .../delivery/read-only-intake.patch` 成功后应用，12 文件、1858 行新增。未导入 synthetic bundle 历史。
- 原生 Codex 工作树工具返回工具错误；检查 Git 工作树列表未产生对应树后，以 Git 创建隔离工作树成功。系统 Python 3.9 不兼容当前 Library helper，使用现有 Python 3.12.14 完成物化；没有修改 helper。
- 唯一源码适配：`knowledge_source_inputs.py` 的 5 个模块导入移至文件顶部，默认 Ruff 的 E402 从失败变为通过；没有变更行为或放宽测试。

## 实际测试

所有 Python 测试使用原项目已有的 `.venv/bin/python`（Python 3.12），以 `PYTHONPATH=apps/api/src` 优先加载本工作树。未导入原工作区未提交模块。测试命令显式移除 `TEST_DATABASE_URL` 和 `DATABASE_URL`，没有连接生产数据库。

```sh
env -u TEST_DATABASE_URL -u DATABASE_URL PYTHONPATH=apps/api/src \
  /Users/song/Documents/Codex/2026-07-19/w/card_platform/.venv/bin/python \
  -m pytest -q --disable-warnings
```

| 运行 | passed | failed | skipped | errors | 秒 |
|---|---:|---:|---:|---:|---:|
| 未应用补丁的完整基线 | 158784 | 150 | 364 | 22 | 239.10 |
| 补丁后首次完整运行 | 158826 | 150 | 363 | 22 | 191.39 |
| 最终导入整理后的完整运行 | 158826 | 150 | 363 | 22 | 180.85 |

172 个失败/错误测试名称完全相同，新增问题为 0。完整基线 159320 个用例，大部分为真实球员字典的参数化测试。22 个 setup errors 来自 `test_account_cloud_sync.py` 的 `pg_dump --schema-only` 访问本机 `/tmp/.s.PGSQL.5432` 被沙箱阻止；没有扩大数据库权限。150 个原有失败主要在估值/身份/来源证据等模块，未纳入本次 CLI 修改。不能把本次结果称为全仓库全绿。

- 四个新增测试文件：41 passed；导入整理后再次运行仍 41 passed。
- 六个既有知识回归文件：89 passed，包含真实 UConn PDF 169 行与版本身份回放。
- 原始 NBA 字典来自完整 Git checkout，SHA256 `3a9c944b0fd7319fef1ec050b72f2c0b022865b73cb9f43ec39c42f692e82f8a`。
- UConn PDF 从原仓库现有采集材料只读复制至工作树测试目录，SHA256 `c98ddf7a3c08e94fbcd2defc46f6f0e195228bb6e2b367d5d642b9f5b5856d4e`，与测试预期一致；未伪造数据，未修改跳过条件。PDF 仅为离线测试材料，不提交为知识库更新。
- 8 个改动 Python 文件默认 Ruff 通过；compileall 与 `git diff --check` 通过。
- fixture 双跑：第一次 parser_calls=1/cache_hits=0/llm_calls=0，第二次 0/2/0；输入哈希不变，plan.json、coverage.json、report.md 逐字节相同。fixture 仅为合成样本，不是云端知识覆盖。
- 本轮没有运行网页/手机构建、设备验证或依赖 PostgreSQL 的完整集成环境；不能据此声称这些范围通过。

专项命令：

```sh
PYTHONPATH=apps/api/src python -m pytest apps/api/tests/test_knowledge_source_inputs.py apps/api/tests/test_knowledge_intake_plan.py apps/api/tests/test_knowledge_coverage_report.py scripts/tests/test_plan_knowledge_intake.py -q
PYTHONPATH=apps/api/src python -m pytest apps/api/tests/test_official_checklists.py apps/api/tests/test_knowledge_text.py apps/api/tests/test_knowledge_card_catalog.py apps/api/tests/test_knowledge_annual.py scripts/tests/test_knowledge_intake_gate.py scripts/tests/test_supplement_knowledge_sources.py -q
PYTHONPATH=apps/api/src python scripts/plan_knowledge_intake.py --input-root ../delivery/fixture/input --baseline baseline.json --scope scope.json --captures captures.json --cache-dir ../verification/fixture-cache --output-dir ../verification/fixture-output
```

完整日志与失败逐名清单保存在工作树旁 `../verification/`，包括 baseline-full.log、baseline-problems.md、patched-full.log、patched-comparison.json、final-full.log、final-comparison.json、final-problems.md、focused-final.log、patched-knowledge.log、fixture-verification.json、ruff-red.log、ruff-green.log。最终完整运行与基线的172个失败/错误名称完全一致，新增为0。失败名称全部保留，未隐藏原有失败。

原工作区保护复验：最初和最终 `git status --porcelain=v1 -z` 的 SHA256 均为 `0665c3cbc4dd05d60f4aa3090958343f4ba00c8912bb4e503633995f3c4f46f3`；最初和最终 `git diff --binary` 的 SHA256 均为 `4d1ef962a869f87347a628af5d9dfe191e3c858b245fd0c3312958b3686e5add`。

## 真实云端快照可用性与覆盖统计

已按卡牌管家技能实际调用 `get_capabilities`、`get_profile`，连接正常，当前账户为 song 的卡牌。当前连接列出卡库、照片、交易、研究与任务工具，未提供知识版本冻结快照或官方附件导出能力。可用浏览器清单中没有可复用的已登录 cardsp.cn 标签。

状态：**真实快照不可用，等待另行授权的服务器只读动作，未执行且未被拒绝。** 具体后续动作：通过现有 `card-platform-deploy` 连接，对 `https://cardsp.cn` 当前发布版本的 `knowledge_version.snapshot`、版本 ID/revision/checksum 和其引用的 official_artifacts 做只读导出与字节校验。不得生成/扩大凭证、读取或披露密钥、部署、发布或写生产数据。本轮未执行 SSH 或数据库查询。

历史 v23 / revision 1853 / version ID `1888f910-0906-4db3-92e7-dc29ba802d59` / checksum `78e1bf3a8400530c264b3ccc5a1f376160debb1d785ac7db75e1d69ce5a730fc` 仅为交接的历史观察，未作为本轮当前值。未把 `/entities` 活动表当冻结快照。

因此真实年度产品分母、跨来源去重基础卡总数、全卡图片覆盖率均未计算；不引用旧 126 产品、702430 版本事实或 858 图片宣称完成率。CLI 未向知识库录入任何内容。

## 边界与裁决

- Ruling: 在独立工作树中直接应用原业务补丁，保留真实上游历史；若与原工作区未提交功能存在后续集成冲突，须单独审核，不能自动覆盖。
- Ruling: 完整测试中原有 150 失败和 22 环境错误保留为未完成项，继续完成已授权补丁验证；代价是本分支不能声称全仓库全绿，也不能据此发布。
- 最后一次独立、只读接入审查未发现 Critical / Important / Minor 问题；范围为交付差异、导入移动、规格和验证结论，没有重做已完成的 CLI 功能审查。
- 没有 push、merge、PR、部署或生产数据写入；没有改动本地运行知识库。
