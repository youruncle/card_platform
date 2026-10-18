# T2 — 逐面识别与共享入库门禁

实现沿用本规格、计划和任务清单，使用测试先失败再实现的流程。没有新增逐面付费模型请求，没有操作真实数据库或服务器；现有未提交工作保留。

## 实现

- `CardRecognitionResult.pair_validation` 可缺省。新结构保存 `consistent/conflict/unknown`、原因、证据、front/back 独立观察、当前图片 SHA-256 和 version。逐面观察包含实际 role、主体名单、厂商、产品、insert、卡号、多主体标记与可见证据。缺字段、格式错误不会编造一致结论；明确冲突不会因格式错误消失。
- `PAIR_RECOGNITION_PROMPT` 替换原先“必为同一卡”的提示，在现有一次双图调用内先分别读图再判关系。服务器重新比较逐面信号，并绑定实际处理后图片内容；模型的 consistent 字符串不能独自放行。
- `pair_integrity.validate_pair_for_confirmation(job_result, image_hashes, review)` 为共享门禁。明确冲突、相同字节双图和过期哈希返回可读 409；未知需要 `pair_review={confirmed:true,image_content_hashes:{front,back}}`。明确单面任务保留原行为。
- Scanner 的未知或冲突双面结果保存为 succeeded 审核草稿，设置 `require_confirmation=true`，在创建库存、成本或估值副作用之前返回。
- Capture 确认在任务行锁内读取当前处理后图片并核验，先提取 `pair_review`，再保留原补拍 `accepted_identity` 精确 body 校验、所有权、幂等和身份接受限制。持久化使用同一批已校验字节。通用 scanner 审核任务可走该入口；没有新增 phone-only 限制。
- Topps/Bowman 与已知厂商拼写差异做兼容归一化；主体全名/中间名兼容，缩写歧义不能仅凭共同 insert 判一致。不明确的产品题名差异进入未知，明确的不同产品仍阻断。
- 经原卡核对的 Impulse #61 Kobe/Corey 和 Generations G11 Vince/Oscar 双主体版式仅降级为需要当前双图人工审核的 unknown。最终 Century Legends + 双面 Generations + Vince/Oscar + 两面卡号均未读出的精确补充由主代理完成；不是一般多主体豁免，不自动确认。

## 测试证据

直接观察到的失败与对应修复包括：新字段被 schema 丢弃；自动任务在缺少配对证据时尝试写库存；malformed conflict 被误当 unknown；共享 insert 导致 A. Thompson/Ausar Thompson 被误判一致；合法 Impulse 正反面被模型按两张正面拒绝；厂商空格变化被误判不同品牌。上述均先运行失败回归，再实现对应修复。

本人最后一次完整定向运行：

```text
.venv/bin/pytest -q apps/api/tests/test_pair_integrity.py apps/api/tests/test_pair_ingestion_gate.py apps/api/tests/test_pair_recognition.py apps/api/tests/test_scanner_worker_timing.py apps/api/tests/test_recognition_stage_timing.py apps/api/tests/test_recognition_null_redemption.py
50 passed in 2.11s
```

日志为 `/tmp/t2-targeted-final.log`。测试涵盖缺模型字段、逐面产品/主体矛盾、重复正面与字节、未知显式审核、图片更换、单面兼容、scanner 无入库副作用、capture 拒绝发生在写入之前、补拍 body 去除审核字段以及幂等。

真实 PostgreSQL 正向测试夹具改为不同 front/back 图片，并提供独立逐面一致观察和实际当前图片哈希；不关闭生产门禁。测试客户端显式关闭 PLAYER_PRICE 等后台 worker，避免隔离测试的后台连接干扰。主代理运行的较大隔离回归记录在 `/tmp/pair-capture-final.log`：69 passed in 15.96s；本人读取日志确认该结果。该运行有 3 个 warning：1 个 Starlette/httpx 弃用提示，以及 2 个超大图片拒绝用例触发的 Pillow DecompressionBombWarning。最终小补丁后主代理运行的定向回归记录在 `/tmp/pair-gate-final-green.log`：35 passed in 1.06s，并报告 Ruff 通过。

## 真实样本与限制

主代理执行付费模型样本调用；本子任务没有执行付费调用。最初 4 个样本中，846 错配、846 恢复后、306 双正面符合预期，849 合法双主体被模型误拒绝。根据原卡证据加入精确版式审核守卫后，冻结原输出可按预期判定。随后 340 的 Century Legends/Generations 缺卡号输出增加了对应红色回归，由主代理完成最后补丁。主代理报告最终 7 份真实模型输出重放为 2 conflict、3 consistent、2 unknown；不将该小样本外推为整体准确率。

未运行整个 API 巨型测试集；没有声称覆盖全部卡种、未观测到的别名或所有同款实体交换。相同球员、产品和没有个体标识的不同实体不能保证识别区分；unknown 需要用户审图。真实模型未读出的字段不伪造为已核验。旧客户端省略未知审核字段会收到 409，需升级客户端。

最终源码和发布提交由主代理统一管理；本子任务仅交付自己负责的实现、测试与此报告，没有执行部署或真实数据修改。
