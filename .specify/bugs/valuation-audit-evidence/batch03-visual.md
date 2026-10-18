# 第三批视觉召回接续

缺陷：生产v7的主模型将背面事实混入正面证据、误标版本字可见性，正常配对大量uncertain；#261把金属反光误读固定色差。沿同一026规格处理，封存审核不重写。

最小修复：独立逐面观察、真实image_ref标签、主/独立提示区分油墨与反射，观察合同v9失效旧缓存。正式门、归一化、未知finish门、背号/语言/分母/固定纹理冲突门均保留。没有按卡ID、标题、产品或色相映射特判。

实际验证：重新执行原19组生产v7基线0个T0；统一最终v9原19组取得5个正常T0，9个负向对照全部rejected。当前#261仍主same/独立fixed_color different，保留拒绝；其他正常误拒仍存在，不宣称整体召回或字段准确率解决，不放行下一批。

旧证据更正：原19组未留provider raw且旧harness覆盖了冻结身份；新的严格回放单独保存。#344 case0才是GP1正常，case1/2为G1反例。一次probe成功不作为最终修复证据。

见specs/026-valuation-audit-remediation/verification-batch03-visual.md及deployment-batch03-visual.md；私有原始请求/响应/原图绑定留在.runtime/releases/20261019-valuation-batch03-visual/，不提交库存、捕获或账号。
