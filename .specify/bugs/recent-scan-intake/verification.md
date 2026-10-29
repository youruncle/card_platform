# 验证与云端回读（2026-10-30）

## 结果

最近两批16张扫描现均为succeeded，关联16个互不重复、存在且未删除的实卡。本次从5张已入库恢复到16张，新增11张：2张由正常worker重试自动完成，9张经当前图片审核走原CaptureService.confirm完成。活跃卡库从909增加到920。新增显示编号为918–928；未重复新增原有5张。

- 16组正反面共32个正式库存图片均存在，SHA-256与对应扫描任务当前图片逐一相同。
- 9张人工核图恢复的原模型结果、当前图片hash、具体核对依据和来源保存在capture_metadata.scan_recovery；操作受owner、任务updated_at、原图hash和未入库状态约束。
- 恢复事务核对920条既有库存（包括软删除行、包含本次worker先恢复的2张）及关联字典内容保持不变。首次恢复因既有球员名称大小写断言失败整体回滚；随后改为姓名casefold等价核对，未改既有姓名。其他身份字段仍精确比较。
- 独立卡牌管家get_card_detail已成功回读库存#921：2021-22 Court Kings Nikola Jokić #4、13/99，两面图片均存在，价格未知。未发起Codex逐卡估值，也未虚构成本。
- Joan Beringer及Sevilla的具体平行版本证据不足，清空推测值并在新卡notes留明；其他尚不确定的平行字段继续为空。上述结果不是所有身份字段准确率报告。

## 代码验证

先失败后修复覆盖：漏逐面结构、补读失败/再次漏结构、明确unknown、明确或独立可推导冲突、原联合身份与补读主体/产品/卡号冲突、路由trace保留、空memorabilia_types、运行后过期任务恢复、不得失败新鲜的最后一次尝试/已入库记录。

- 相关路由、配对、worker、schema回归：88 passed。
- image_service及交叉回归：40 passed；与上组重叠24项，共104个不同用例通过。
- Ruff与本任务git diff --check通过。
- 独立审查复现“补读身份与原草稿不同仍可能放行”；已新增3项失败回归并修复。复审16项通过、无遗留Important；后来追加的null物料类型修复已另行实测。
- 真实Ja Morant Splash! #15在旧解析下复现memorabilia_types:null导致ValidationError；候选相同图片返回两次模型响应、补齐front/back后通过门禁。原始HTTP返回、finish_reason和usage保存于私有回执。

全量API命令实际启动后于151秒中断：1387 passed、122 failed、98 skipped，未完成。122个失败节点全部在临时隔离包中还原本次四个源文件到HEAD后重跑，仍全部失败（4.50秒）；节点明细见[broad-test-failures.txt](broad-test-failures.txt)，不冒称全仓绿色。主要涉及已有估值/市场证据门与身份回归，未将这些模块扩入本次修复。

## 发布保护

线上API镜像：card-platform-api:0.6.5-20261030-scanfix1。运行253个Python源逐SHA与候选一致，仅4个任务文件相对原镜像变化，其余249个保持。生产只增加releases/20261030-scanfix1/compose.scanfix.yaml镜像覆盖，沿用原tunnel、chatgpt和purchase叠加配置；.env.cloud发布前后SHA不变。临时runtime.env已删除。

运行容器实测PURCHASE_COST_WORKER_ENABLED/settings为true；视觉deepseek-flash、thinking.type=disabled、vision_thinking_enabled=false；qwen-plus搜索独立百炼配置有值且端点符合校验。API ready和公网首页均HTTP200。卡牌管家首次整页检索超时，随后单卡真实回读成功；没有把超时算作通过。

## 未覆盖

未重新驱动物理扫描仪采集新图；实测使用这批保留原图回放及恢复。未重新构建/覆盖安装iPhone，本次客户端源码未改，共用已发布API。未运行完API全量测试，依赖独立测试数据库或私有样本的跳过项不计通过；未验证全部具体平行版本、评级/品相或市场估值。未更新本地运行卡库或任何知识库内容。
