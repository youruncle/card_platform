# 篮球卡知识库 v5 逐项证据审计

本审计遵循“分类、规范名、别名、产品归属、赛季适用性分别取证”。产品 checklist
只能证明其中实际出现的产品、insert、parallel 或卡片，不能自动证明中文卖家用语。

| 对象组 | 审计结论 | 处理 |
|---|---|---|
| Bowman | Fanatics 官方明确称为 Bowman brand | 保留为 `brand`，替换错误来源 |
| Bowman Chrome | 篮球语境会混淆 Bowman University Chrome 与 Bowman Basketball 的 Chrome variant | 停用宽泛产品名，待按正式产品+赛季重建 |
| Topps / Fanatics | 把品牌和所有者拼成 manufacturer 不规范 | 改为 manufacturer `Fanatics Collectibles`；`Topps` 单列 brand |
| Panini | 2023–24 checklist 可证明 Donruss、Optic、Prizm 的制造商 | 保留 manufacturer；停用重复 brand |
| Donruss / Donruss Optic | Beckett checklist 分别存在，纸质与 chromium 产品不可合并 | 保留及排斥关系 |
| Prizm | 2023–24 Panini Prizm checklist 直接支持 | 保留 |
| National Treasures / One and One | 原 Prizm 来源不成立；各自 checklist 可直接支持 | 更换为各自 2023–24 checklist |
| Court Kings / First Steps | Panini 2019–20 官方 PIS 明确 First Steps 是 Court Kings insert，并展示新秀赛季球鞋 | 恢复产品与 insert，记录赛季和视觉特征 |
| Topps Midnight / Dreamland / Night Owls | Beckett 2025–26 checklist 直接列出两个 insert 和赔率 | 更换来源，限定产品与赛季 |
| Rated Rookie / Downtown | Donruss/Optic checklist 支持规范身份 | 保留规范名；无语料支持的中文卖家别名停用 |
| RC / AUTO / RPA / Patch / On-card | checklist/PIS 能支持领域概念存在 | 保留规范属性；中文卖家词需单独语料证据 |
| SSP / Case Hit | 只能在具体产品、赛季和 odds 语境判断 | SSP 保留为需上下文属性；通用 Case Hit 实体降级停用 |
| 大比例 / 大窗 / 三窗 | 是视觉或物料窗口描述，但当前引用页面没有定义 | 实体停用，待参考图/标题语料批次验证 |
| Base、Black、Blue、Gold 等全局 parallel | 同名颜色跨产品和赛季语义不同，v2 又只有模型来源 | 全部停用；后续按 product+season+checklist 建模 |
| v2 其他 insert/product | 只有模型整理来源 | 全部停用，找到直接 checklist 后逐条恢复 |
| 中文俗称/误写 | 厂商 checklist 不能证明卡圈俗称 | 默认停用；鞋灯、梦幻之地、夜莺、猫头鹰暂以历史标题回放候选保留为 0.80 |

## 当前直接来源

- Fanatics Brands：Topps 与 Bowman 的品牌分类。
- Panini 2019–20 Court Kings Hobby PIS：Court Kings、First Steps、球鞋视觉及 on-card auto。
- Beckett 2025–26 Topps Midnight checklist：Dreamland、Night Owls、产品归属和赔率。
- Beckett 2023–24 Donruss、Donruss Optic、Prizm、National Treasures、One and One checklists。

## 后续门槛

任何恢复或新增记录必须同时通过：直接来源、产品/赛季作用域、数据库现状冲突、别名语料证据、
固定案例回放。中文俗称不得再继承英文规范名的 checklist 来源。
