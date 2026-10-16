# 背面贴边回归与 v22 修复（2026-09-22）

## 对上次验证的纠正

v21 改的是正反面共用检测器，并非只有正面。数据库确认失败背面 `6510b71e-08d9-4d21-9ed6-d019896647f7` 的 detector_version 为 v21，slot_refinement 为 independent_back_geometry，SAM2 回退保留了四个网格碎片。
上次验证只覆盖用户当时给出的正面原图，没有覆盖本次背面贴底边情况；未实际运行生产配置下的正反双面 SAM2/方向/OCR 链路。不能把正面样本通过等同于背面场景已验证。

## 根因与修复

背面下方卡套贴近扫描底边，矩形拟合角点最高 y=3520.74，原图高3507，约0.39%越界；两档阈值填充率均约0.989，完整轮廓证据稳定。v21 对任何边缘接触直接否决，导致整页退回4×2网格并把两卡切成四块。

v22 复用已有 SOURCE_BOUNDS_SEVERE_OVERFLOW_RATIO=1%：允许轻微拟合越界进入完整对象路径，最后用现有统一坐标裁限限制在原图内，并保留 source_edge_contact。显著越界仍不能走此容差路径，未放宽比例、跨阈值一致性、剩余前景等检查；不补造原图之外的像素。

## 验证

- 先观察四边贴边测试及真实背面测试5项失败，再修改生产代码；之后通过。
- 最终专项16项通过，包括四边贴边、显著越界拒绝、正背面 store_sheet、错误旧正面张数不传递至背面、真实双面卡面覆盖和前次紧贴防回归。
- 独立审查未发现生产代码阻断项；修正前面回归用例的文件路径，明确固定 front sheet，避免新增 back 原图被不确定的 glob 顺序误选。
- API 全量：2108 passed，67 skipped，1条既有 Starlette/httpx 弃用警告。随后新增的显著越界测试也已通过专项运行。
- 生产配置真实链路影子回放：SAM2、Apple Vision 朝向、OCR、透视变换和 JPEG 均真实运行；仅数据库连接替换为写入隔离模拟，未动正式数据。正面/背面各2张，SAM2各2个 refined，质量 error_count=0。
- 正面整卡最低覆盖0.99524、背面0.98563（SAM2结果相对独立检测轮廓）；目视查看背面两张完整裁图。
- 背面仍有1张 source_edge_contact 警告、needs_review_count=1；不是无警告的完整采集保证。身份识别联网核验和新硬件采集未运行。
- 证据与裁图：`work/rotated-back-fix/`，含 shadow-service.log、front-service.json、back-service.json、api-tests.log。

## 回放、部署与当前草稿修复

最终历史回放：115份原图、SHA/DPI/计数标签去重后76例，76/76通过，regressions=[]；目标背面新增哈希、尺寸、手标卡面多边形和97%覆盖/唯一匹配约束。报告 replay.json。

确认后台无待处理任务后重启 launchd API 加载v22。健康、数据库就绪、Web均200，未认证扫描接口保持401。

对当前未提交会话进行带锁修复：固定 owner/session、原背面sheet、最新正面sheet、updated_at、原图SHA，拒绝覆盖人工布局或用户并发修改。数据库写入前在0600权限文件 draft-before.json 保存会话、所有sheet/regions及旧配对。基于保留原图生成 back revision 2（6f7cfadb-8fb8-4141-bfa0-204501c08779），实际SAM2/朝向/OCR完整运行，2张卡、0个质量错误、1个贴边提示。旧back revision1和所有正面修订保留；只重建配对建议，未自动确认或录入卡库。提交后只读查询确认最新正面2张、背面2张、requires_review=true、submitted_at=null。

部署和修复证据：deployment.json、repair-draft.log、draft-before.json、draft-after.json。页面重新加载会恢复该最新未完成会话。
