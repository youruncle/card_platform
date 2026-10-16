# ID 586：Roses 卡种缺失导致估值漏搜

## 复现与根因

用户提供 https://cardhobby.com.cn/market/item/122156847 。本次只读查询数据库：最新身份为 Topps / Signature Class / 2026 / Tracy McGrady / R-5，insert_name 为空；最近估值为 insufficient_evidence。估值诊断明确包含“目标为基础卡/未识别卡种，但标题明确为命名特卡或变体”。旧看图审阅文件已识别 Roses，但当前结构化身份没有该字段；不把旧模型输出直接当作已确认身份覆盖数据库。

公开商品页与 SearchCommodity 返回一致：Status=-2，ByWay=2，17 次出价，结束时间 2026-07-12 21:00:09，卡价 131.11 元，运费 18 元，标题“轻印见图”。属于有出价的结束拍卖，未核实付款，且有品相瑕疵。

原始身份匹配该商品得分 0；仅补全 insert_name=Roses 后得分 .77、无标题身份冲突。搜索接口需现有 provider 使用的 XMLHttpRequest/Referer headers；不带 headers 的请求返回空列表，不能以此推断市场无成交。本次 httpx 直连遇到连接中断，实时回放用 curl 携带相同查询参数与请求头访问公开接口。

## 最小修复

- 在共用 CardIdentityResolver 中，只有缺失 insert、Topps Signature Class、审核过的发行年份与 R-1 至 R-25 卡号组合才补全 Roses。适用于该清单家族，不绑定 ID 586 或球员，不推断平行、印量、签字或价格。
- 保留 catalog 来源 URL 和推断理由；已存在卡种及实物证据优先。不写入真实卡库身份。
- 共用卡种词库及卖家检索词加入 Roses / 玫瑰 / 玫瑰特卡；保持 12 请求预算。
- 不改变成交状态、异版、限编、图像与品相门禁。

清单依据：Topps 官方产品页 https://www.topps.com/pages/topps-signature-class-basketball 所链接的官方 PDF 第 10 页：
https://cdn.shopify.com/s/files/1/0662/9749/5709/files/2025-26_Topps_Signature_Class_Basketball_Checklist.pdf?v=1777313247

## 验证与范围

先写回归测试：5 failed / 10 passed，失败对应卡种缺失及商品无法通过身份匹配。修复后加入边界与正式估值品相保护测试。

实时回放见 live-replay.json：实际生成的“麦迪 Roses”召回 18 条，其中 14 条通过候选层；“麦迪 玫瑰”召回 33 条，其中 18 条通过候选层。两组均包含用户提供的 122156847。这些是候选数量，不是最终可定价成交数量。

本次未执行全量数据库集成测试、模型逐图核验、服务重启或正式估值落库。131.11 元的瑕疵拍卖仍被正式正常品相估值门禁拒绝；不以单笔瑕疵拍卖覆写 ID 586 的估值。完整端到端估值需在运行进程加载代码后刷新市场证据并完成原有图像核验。

最终验证：相关身份归一化、卡淘召回、跨品牌冲突、可比分层、签字声明与图像品相门禁共 453 项测试通过（5.50 秒）；本次改动 git diff --check 通过。
