# valuation-quality1 源码发布对应记录

本记录只补齐已部署质量修复的Git发布环节，不重建或重启线上服务，不重新估值，不修改已封存的最终改善结论。

源码提交：`6bf57e0114796f526e66e9e25d8333933ada0c2b`，父提交为远程任务分支原头 `9f31319d0422f1efc2b59e6c2a5ac419f908d942`。发布分支仍为 `codex/valuation-audit-remediation`。源码提交仅包含5份任务源码和3份回归测试；本记录、对应机器证明及同一026规格/计划/任务/最终报告在后续文档提交中保存。

主工作区的HEAD、索引及其他未提交成果保持原状；发布使用独立索引。没有全仓暂存，没有把成本或正背匹配的未提交整文件夹带进入任务提交。

## 与实际部署源的对应

- 实际部署父镜像为history10，SHA `a1f9d50264887d5537417208e57a3ced46024310180c14e748b5b33fe5d9b4ae`，具有186份Python源。
- 已部署API镜像为valuation-quality1，SHA `f1502e2489b01a5c8823657ce47fecddd9707e53f4c2d8d9e017fb6b6e3444f1`。将[本次任务补丁](quality1-task.patch)重新应用到实际history10源后，所得187份源码SHA逐项等于已验收部署清单；182份父源未变。
- `sale_independence.py`、`valuation_worker.py`、`comparables.py`在远程原头和history10完全一致。新读取合同为新增文件。
- `repository.py`的history10父源另有8行既有自动成本入队代码，远程原头尚无该段。本次提交明确排除这8行，只接入读取合同的5行改动。将这段已有父版本差异单独加回源码提交的repository后，字节SHA等于已部署文件；该差异没有作为本次源码修改提交。

完整父源/部署187源清单、提交文件SHA、任务补丁SHA、独立索引树及repository对应证明见[机器收据](publication-quality1.json)。最终文档提交头和远程实际分支头在本地私有`continuation-20261019/git-publication/push-receipt.json`留存，以避免自引用提交SHA。

## 验证

独立索引生成的真实发布树重新导出并测试：43通过、13跳过。跳过的是需要隔离PostgreSQL的测试，本次仅补Git发布，未重启已清理的本地数据库；实际部署上下文此前14项隔离SQL及真实shadow检查已通过。部署冻结源完整API结果为158020通过、134项既有失败、250跳过，新增失败0；其他证据和数值真值局限保持[最终报告](verification-batch04-revaluation.md)中的判定。

当前线上源没有因此改变，没有新的部署、服务重启、生产数据写入或任务入队；最后77张仍未启动。
