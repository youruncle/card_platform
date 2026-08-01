# 篮球卡知识库覆盖母表口径

## 目的

母表用于估算 1996–至今 NBA 主流产品知识的目标总量，不把“数据库里已有多少行”
误当作完整度。母表的最小单元是：

`manufacturer × brand/product × season × facet`

其中 `facet` 固定为 `base`、`parallel`、`insert`、`auto_relic`、`card_identity`。
每个单元还必须记录 `source`、`evidence`、`confidence` 和 `context`。没有逐赛季直接证据的
候选单元只能进入规划母表，不能进入发布快照，也不能计为 `covered`。

## 范围与归属

- 时间：1996–97 至当前已发布正式 checklist 的 NBA 赛季；预览但未发布 checklist 的赛季
  单独计为 `awaiting_checklist`。
- manufacturer：Panini、Topps、Upper Deck、Fleer、SkyBox、Leaf。数据库可另存企业所有者，
  但统计时映射回本母表范围。
- Bowman 是 Topps 旗下 brand，不是 manufacturer。
- Donruss 与 Donruss Optic 是不同 product series，禁止合并。
- SkyBox 按卡面/正式 checklist 的发行身份保留产品归属，并另用 ownership/context 记录
  Fleer 或 Upper Deck 的历史公司关系，禁止用公司并购反向改写卡片发行身份。

## denominator 估算算法

1. 以官方厂商 checklist/产品页为第一优先，Beckett、Cardboard Connection、TCDB 为补充，
   建立实际发行的 `product-season` 清单；同名预览页不算正式 checklist。
2. 每个经证实的 `product-season` 展开 5 个 facet，因此：
   `denominator = verified_product_seasons × 5`。
3. facet 只在证据明确不适用时记 `not_applicable`；缺少证据时记 `unverified`，不可用上一赛季
   外推。
4. `base` 需有基础卡 checklist；`parallel` 需有该产品赛季的平行版本表；`insert` 需有插入卡
   checklist；`auto_relic` 需分别核对签字、球衣/patch/material checklist；
   `card_identity` 需有关键卡号、球员、RC/insert/parallel/serial/material/auto 组合证据。
5. 汇总时同时报告：
   `covered / denominator`、`partial / denominator`、`unverified / denominator`、
   `awaiting_checklist`，不得把数据库占位行计入 denominator。

## 属性与稀有度约束

大比例、大窗、三窗、盾牌、tag、logoman、on-card、sticker auto、game-used、player-worn
属于视觉、签字方式或物料属性，不是默认 parallel 或 SSP。`SSP` 与 `case hit` 分开建模，
只有具体产品、赛季和赔率/厂商措辞支持时才建立关系。Jumbo/大比例不自动等于 SSP。

## 当前基线与首批缺口（2026-07-29）

数据库物理覆盖行为 `covered 566 / partial 100 / pending_source 11`。其中 9 条
`pending_source` 是无来源、无赛季的旧规划占位；可审计的待来源项实际为 2 条：
2025–26 Panini Prizm Basketball 和 2025–26 Panini Origins Basketball，二者均只有预览来源，
应保持 `awaiting_checklist`，不得补推。

第一批执行顺序：

1. 退役上述 9 条无来源、无赛季占位，不改变任何 `partial` 或 `covered`。
2. 以 2023–24 Donruss、Donruss Optic、Prizm 为第一组 denominator 样板，分别核对 5 facets，
   且保持 Donruss/Optic 分离。
3. 以 2024–25 Select、Mosaic、National Treasures 为第二组，优先补
   `auto_relic` 与 `card_identity`，同时回归 animal parallel、RPA、盾牌/tag/logoman 等属性。
4. 以 1996–97 Upper Deck、Fleer、SkyBox、Topps 为历史样板，先确认 manufacturer/brand/
   ownership context，再展开 facets。

本文件只定义估算口径和执行队列，不声称母集已经完整；每次新增 product-season 都必须附
直接来源后才增加 denominator。
