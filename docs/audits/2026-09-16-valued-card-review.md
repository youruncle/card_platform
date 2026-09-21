# 已估值卡片价格与证据复审

审计开始：2026-09-16T04:32:29.999994+00:00；结束：2026-09-16T05:32:24.866562+00:00。仅卡淘。

账户基线519张；纳入曾有自动估值的388张，其中最新成功361张、已证据不足27张。
原未提交修改、身份和历史估值快照均保留。仅追加本轮任务与符合质量门的新估值。

## 结果与边界

维持不足 26张；转证据不足 268张；下调 25张；维持 49张；上调 20张。
维持合计 75 张（含已不足且仍不足）。
比较基于原自动估值快照；原最新状态另列。没有新价不是估值为零。

388张均执行正反面模型复核与本地Apple Vision OCR；逐条检查3222条历史候选，按各卡身份共复核3392次；旧估值共有1283次商品引用。
另有#49的旧PSA人口报告引用，缺少对应卡淘成交记录，已排除于本轮估值。
两轮共776条卡淘域名查询，保存1682个去重商品页线索。召回数不是独立成交数或同款成交数。
卡淘直连搜索接口不可用。原1182个唯一引用商品页均尝试重读，86个页面可解析，85个有日期、价格及出价资料。其余未声称已完成当前页面状态核验。
所有有出价结束拍卖仅作为落槌价记录，未宣称已确认付款。挂牌、无出价、不同卡、未明确落槌价均不补值。

**独立核验的限制：表中价域是已核对同款历史落槌价的观测范围，不是第二套独立公允估价。单条记录或来源受限时，无法据此建立完整近期合理价区间；本轮未能完成用户要求的全量近期独立区间核验。**

## 共性问题与增量修复

- 正式估值不再发布同产品类比或评级卡的裸卡参考价；只有同卡同评级T0/T1证据可进入价格中心。
- 全量回放后的大幅变价抽查发现未知平行仍可被图片相似放行：新增门禁，目标平行为空时不接受明确银折、紫折、紫波纹等版本；相同限编也不能补足版本身份。追加修正回放并保留中间轮次。
- 旧审核标记不能绕过零出价、固定价、未来日期、无时区、未证实付款及异常汇率门禁。
- 缺失/为零的结束价不能回退到起拍价；明确零出价不能被其他计数覆盖；异常单行不再导致整次解析崩溃。
- 可比图片同时使用目标正背面，检查卡号、限编和产品信息；图片下载使用有界并发，平行差异不能称为普通变体。
- 排除披露损伤、印刷线等品相差异及不保证品相的案例；保留无明显损伤等否定语义。
- 卡淘www/apex域名及路径大小写按商品ID归一，避免同一拍卖因URL表现不同被漏用。
- 修复身份复核缺失交换板字段被写成null的问题，保留确认版本/识别中的兑换状态；更新过期认证集成测试。
- 模型把统计赛季、缺失字段、RC标识或数值8与8.0误报冲突的情况单独过滤。原始观察保留，不据此自动改身份。

历史候选拒绝/待核验原因（可重叠）：not_exact_or_near_exact_same_grade: 2972；same_version_thumbnail_not_confirmed: 2259；cardhobby_discovery_candidate_not_same_card_evidence: 2139；visual_identity_conflict: 1577；cardhobby_source_payload_not_verifiable: 829；parallel_conflict: 820；disclosed_condition_damage_requires_separate_valuation: 784；target_parallel_unknown_for_explicit_listing_variant: 667；specific_parallel_not_confirmed: 541；insert_conflict: 369。

## 交付文件与代码

- [逐条证据与逐卡结果 JSON](/Users/song/Documents/Codex/2026-07-19/w/card_platform/docs/audits/2026-09-16-valued-card-review.json)
- [原始审计产物](/Users/song/Documents/Codex/2026-07-19/w/card_platform/work/audits/automation-2-20260916)：包含两轮查询、页面记录、正反面复核和逐批回放。
- 主要修改：comparable_image_verifier.py、valuation_worker.py、formal_cardhobby_gate.py、domain/auction_evidence.py、domain/valuation_quality.py、ingestion/cardhobby.py、identity_review_api.py。
- 新增审计/回放/报告脚本与两组价格完整性回归测试；另更新过期认证测试和两处Ruff格式。
- [基线文件保留检查](/Users/song/Documents/Codex/2026-07-19/w/card_platform/work/audits/automation-2-20260916/preservation-check.json)：无文件丢失，列出增量修改的原有文件。

## 测试与服务

- ruff: All checks passed!
- related: 43 passed in 0.72s
- full: 1698 passed, 27 skipped, 1 warning in 40.43s
- postgres_full: 1724 passed, 1 skipped, 1 warning in 58.49s
- 服务：{"checked_at": "2026-09-16T05:32:23.804853+00:00", "health": {"status": 200, "body": {"status": "ok"}}, "ready": {"status": 200, "body": {"status": "ready"}}, "web": {"status": 200, "body": "HTML returned"}}
- [数据库核验](/Users/song/Documents/Codex/2026-07-19/w/card_platform/work/audits/automation-2-20260916/database-verification.json)：全部最新任务、历史快照保留、采用来源、版本等级及价格区间一致性已检查。
- 未修改前端源码；本轮无需前端typecheck/build。

## 需核验的卡片

优先补拍#4背面、#12背面、#14正面：人工抽查见明显局部裁切/背景占比过大，不能视作完整身份已核实。
优先确认#16、#486的平行版本：银折与普通版、紫折与紫波纹不可混用，版本未确认前不发布对应候选价格。
本地OCR与视觉模型同时提示的字段差异见下表；这些仍是核验线索，身份未被自动覆盖。

| ID | 字段 | 原记录 | 图像/OCR观察 |
|---|---|---|---|
| 94 | serial_number | — | 172 |
| 97 | card_number | 290 | 203 |
| 113 | serial_number | — | 32 |
| 123 | serial_number | — | 65 |
| 149 | serial_number | 93 | 94 |
| 162 | card_number | — | 80 |
| 190 | is_memorabilia | False | True |
| 206 | card_number | 24 | 15 |
| 226 | card_number | 30 | 18 |
| 228 | is_autograph | False | True |
| 232 | card_number | 30 | 18 |
| 255 | serial_number | — | 81 |
| 255 | serial_total | — | 150 |
| 382 | is_memorabilia | False | True |
| 443 | card_number | — | 22 |
| 490 | serial_total | — | 125 |

其他图像差异、每条引用的原始标题、日期、价格和匹配/拒绝原因均记录在同名JSON中。

## 逐卡结果（CNY，卡片价，不含运费）

| ID | 卡片身份 | 原合理价〔区间〕 | 新合理价〔区间〕 | 独立证据价域* | 变化 | 实际采用条数 |
|---|---|---|---|---|---|---|
| 0 | JAREN JACKSON JR. / 2020 / Mosaic Basketball / Fusion Red and Yellow Choice Mosaic #29 /88 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 1 | LeBron James / 2025 / NBA Hoops Basketball / Bounce House #BH-2 | 45.0〔29.25–60.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 4 | ACE BAILEY / 2025 / Topps Chrome Basketball / Basketball Refractor #255 | 40.9〔26.59–55.22〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 5 | Andrew Nembhard / 2023 / Donruss Elite Basketball / Status #83 /8 | 168.0〔125.0–210.0〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 6 | Stephen Curry / 2025 / Topps Signature Class Basketball / Star Cast #SC-1 | 400.0〔340.0–460.0〕 | 330.00〔280.50–460.00〕 | 330.00–400.00 | 下调 | 2 |
| 9 | SCOTTIE BARNES / 2025 / Flagship / Gold Diamante #31 /50 | 45.6〔16.0–55.88〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 10 | Jaren Jackson Jr. / 2020 / Mosaic Basketball / Fast Break Purple #29 /50 | 115.0〔85.0–145.0〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 12 | BRANDON MILLER / 2023 / Prizm Basketball #152 | 1.0〔0.85–63.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 14 | SCOTTIE BARNES / 2025 / Flagship #31 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 15 | Luka Doncic / 2025 / Topps Chrome Basketball / Red, White, and Blue Refractor #2 | 12.0〔7.8–16.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 16 | VJ Edgecombe / 2025 / Topps Chrome Basketball #153 | 10.0〔6.0–11.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 17 | Kevin Garnett / 2025 / Panini Signature Series Basketball / Plaid #94 /15 | 76.0〔49.4–102.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 18 | Matas Buzelis, Tristan da Silva / 2024 / Panini Immaculate Collection Basketball / Remarkable Rookie Jerseys Dual / Blue #12 /25 | 114.82〔74.63–155.01〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 19 | AJ DYBANTSA / 2025 / Topps Chrome McDonald's All American Games / McDonald's All American #3 | 3.01〔2.56–4.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 20 | AJ DYBANTSA / 2025 / Topps Chrome / McDonald's All American #3 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 21 | LeBron James / 2025 / Topps NBA Hoops / Bounce House / Silver #BH-2 | 27.0〔22.95–31.05〕 | 12.00〔7.80–16.20〕 | 12.00–12.00 | 下调 | 1 |
| 22 | Victor Wembanyama / 2024 / Panini Prizm Basketball / Fractals #10 | 17.0〔14.45–30.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 23 | Victor Wembanyama / 2024 / Panini Mosaic Basketball / Jam Masters #4 | 16.0〔10.4–21.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 24 | Victor Wembanyama / 2024 / Panini Donruss Basketball / Bomb Squad #6 | 5.0〔3.25–6.75〕 | 5.00〔3.25–6.75〕 | 5.00–5.00 | 维持 | 1 |
| 25 | Victor Wembanyama / 2024 / Panini Donruss Basketball / Magicians #9 | 9.0〔7.65–13.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 26 | Scoot Henderson / 2023 / Panini Immaculate Collection Basketball / Gold #98 /10 | 199.99〔129.99–269.99〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 27 | Justin Champagnie / 2021 / Chronicles Draft Picks / Hoops Retro Autographs #HR-JCH /199 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 28 | Zach LaVine / 2016 / Preferred Basketball / Silhouettes Autograph Relics #44 /60 | 252.0〔214.2–289.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 29 | Jalen Duren / 2023 / National Treasures Basketball / Penmanship #P-JDR /25 | 310.0〔263.5–356.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 30 | BRANDON INGRAM / 2024 / One and One / Jumbo Jersey Autographs #JJA-ING /75 | 208.0〔176.8–246.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 31 | NATE AMENT / 2025 / Topps Chrome McDonald's All American Games / Winning Tags #WT-NA | 71.0〔46.15–95.85〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 32 | VJ Edgecombe / 2025 / Court Kings / Rookies III #167 | 51.82〔39.0–62.0〕 | 39.00〔33.15–62.00〕 | 34.00–62.00 | 下调 | 3 |
| 33 | Victor Wembanyama / 2024 / Mosaic Basketball / Stare Masters #15 | 13.0〔11.05–14.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 34 | AJ Dybantsa / 2025 / Topps Chrome McDonald's All American / Chrome Refractor #45 | 32.0〔20.8–43.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 35 | Dylan Harper / 2025 / Topps Chrome Basketball / Loading... / Aqua Refractor #LD-12 /199 | 242.0〔159.0–278.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 36 | AJ Dybantsa / 2025 / Topps Chrome McDonald's All American #7 | 9.0〔5.95–10.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 37 | AJ Dybantsa / 2025 / Topps Chrome McDonald's All American / Red Lava Refractor #45 /125 | 182.0〔154.7–209.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 38 | Paolo Banchero / 2025 / NBA Hoops / Oasis #O-17 | 352.15〔228.9–475.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 39 | Luka Doncic / 2023 / One and One #97 /99 | 274.24〔207.79–315.88〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 40 | Scoot Henderson / 2023 / Immaculate Collection / Gold #98 /10 | 199.99〔129.99–269.99〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 41 | Shaedon Sharpe / 2022 / Crown Royale Basketball / Silhouettes #112 /199 | 252.0〔214.2–289.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 42 | Shareef Abdur-Rahim / 2000 / Fleer Showcase #74 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 43 | Grant Hill / 2000 / Fleer Showcase #86 | 65.0〔48.0–88.0〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 44 | Toni Kukoc / 2000 / Fleer Showcase #63 | 19.0〔12.35–25.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 45 | Jerome Williams / 2000 / Fleer Showcase #11 | 12.0〔8.5–16.5〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 46 | Glenn Robinson / 2000 / Fleer Showcase #22 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 47 | John Stockton / 2000 / Fleer Showcase #90 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 48 | Tim Thomas / 2000 / Fleer Showcase #18 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 49 | Eddie Robinson / 2000 / Fleer Showcase #68 | 2.85〔2.42–3.28〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 50 | Michael Finley / 2000 / Showcase #34 | 11.0〔7.15–14.85〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 51 | Shawn Marion / 2000 / Showcase #46 | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 52 | Bryon Russell / 2000 / Showcase #5 | 3.0〔1.35–4.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 53 | Allen Iverson / 2003 / Bowman Rookies and Stars #10 | 9.0〔5.85–12.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 54 | Allen Iverson / 1997 / Stadium Club #125 | 5.0〔3.1–5.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 55 | Allen Iverson / 1998 / UD Choice / Starquest / Blue #SQ20 | 32.0〔20.8–43.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 56 | Tracy McGrady / 2009 / Playoff Contenders / Award Contenders #15 | 29.0〔18.85–39.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 57 | Tracy McGrady / 1998 / Finest #28 | 11.0〔9.35–12.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 58 | Tracy McGrady / 1998 / Upper Deck / Super Powers #S26 | 7.0〔3.4–8.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 59 | Kevin Garnett / 1997 / Metal Universe #109 | 26.1〔22.19–30.76〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 60 | LeBron James / 2020 / Mosaic Basketball / Stained Glass #1 | 1335.14〔1061.0–1535.41〕 | 1335.14〔867.84–1802.44〕 | 1335.14–1335.14 | 维持 | 1 |
| 61 | Reggie Miller / 1996 / Collector's Choice / Assignment: Jordan #MJ23 | 3.02〔1.96–4.08〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 62 | Michael Jordan / 2001 / MJ's Back #MJ-90 | 19.0〔12.35–25.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 63 | Shai Gilgeous-Alexander / 2025 / NBA Hoops Basketball / Oasis #O-1 | 421.0〔357.85–537.0〕 | 421.00〔273.65–568.35〕 | 421.00–421.00 | 维持 | 1 |
| 64 | Luka Doncic / 2024 / Court Kings Basketball / Dressed to Impress #3 /125 | 60.01〔45.0–85.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 65 | KEVIN DURANT / 2020 / Mosaic Basketball / Stained Glass #3 | 341.0〔289.85–392.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 66 | Bill Russell / 2025 / Bowman Basketball / Retrofractor Variations #RV-BR | 310.0〔168.33–356.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 67 | Victor Wembanyama / 2025 / Cosmic Chrome Basketball / Extraterrestrial Talent / Refractor #ET-7 | 136.0〔115.6–250.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 68 | Amen Thompson / 2023 / Hoops / Rookie Special #1 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 69 | Victor Wembanyama / 2023 / Topps Now / Rookie of the Year #VW-6 | 160.0〔136.0–184.0〕 | 151.00〔98.15–203.85〕 | 151.00–151.00 | 下调 | 1 |
| 70 | Cade Cunningham / 2025 / Signature Series Basketball / Perpetual Talents / Gold #3 /10 | 216.0〔140.4–291.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 71 | Allen Iverson / 2000 / Upper Deck #123 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 72 | Allen Iverson / 1998 / UD Choice / Starquest / Blue #SQ20 | 32.0〔20.8–43.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 73 | Jayson Tatum / 2019 / Contenders Optic / Superstar #8 | 22.04〔14.33–29.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 74 | LeBron James / 2024 / Prizm #130 | 11.0〔8.0–12.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 75 | Tracy McGrady / 1999 / SkyBox Premium #8 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 76 | Michael Jordan / 1998 / Black Diamond #8 | 13.0〔10.01–14.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 77 | Michael Jordan / 1998 / Black Diamond #13 | 12.0〔9.0–28.83〕 | 9.00〔5.85–12.15〕 | 9.00–9.00 | 下调 | 1 |
| 78 | MICHAEL JORDAN / 1998 / Black Diamond #12 | 26.0〔15.0–33.53〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 79 | LUKA DONČIĆ / 2025 / NBA Hoops / Hoopastic #HN-10 | 4.0〔2.6–5.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 80 | BRANDON INGRAM / 2024 / One and One / Jumbo Jersey Autographs #JJA-ING /75 | 208.0〔176.8–246.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 81 | Kobe Bryant / 1997 / Metal Universe #2 | 173.0〔110.0–243.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 83 | NATE AMENT / 2025 / Topps Chrome McDonald's All American / Winning Tags #WT-NA | 71.0〔60.35–129.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 84 | Victor Wembanyama / 2024 / Mosaic / Stare Masters #15 | 14.2〔12.07–16.33〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 85 | Anthony Davis / 2025 / NBA Hoops / Bounce House /50 | 103.0〔31.96–118.45〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 86 | VJ Edgecombe / 2025 / Topps Chrome Updates Basketball / Moment In Time | 13.0〔11.05–14.95〕 | 14.00〔6.01–16.10〕 | 6.01–67.00 | 上调 | 4 |
| 87 | Paolo Banchero / 2025 / NBA Hoops / Oasis | 156.0〔101.0–352.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 88 | VJ Edgecombe / 2025 / NBA Hoops / Bounce House #BH-18 | 14.0〔11.0–16.1〕 | 20.00〔16.00–23.00〕 | 16.00–22.00 | 上调 | 3 |
| 89 | Pascal Siakam / 2025 / Topps Chrome / Green #BLACK-ON /99 | 5.0〔4.25–11.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 90 | Zach LaVine / 2016 / Preferred Basketball / Silhouettes #44 /60 | 192.0〔163.2–233.0〕 | 192.00〔124.80–259.20〕 | 192.00–192.00 | 维持 | 1 |
| 91 | DYLAN HARPER / 2024 / Topps Chrome / X #252 | 480.0〔10.0–552.0〕 | 480.00〔312.00–648.00〕 | 480.00–480.00 | 维持 | 1 |
| 92 | Jayson Tatum / 2025 / NBA Hoops / Bounce House / Gold #BH-10 /50 | 154.0〔130.9–265.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 93 | Bronny James Jr., LeBron James, Anthony Davis / 2024 / Origins Basketball / Nucleus / Red #11 /115 | 104.0〔88.4–119.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 94 | SHAEDON SHARPE / 2025 / Topps Chrome #106 /399 | 3.08〔2.62–16.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 95 | VJ EDGE COMBE / 2025 / Topps Chrome | 7.2〔6.12–14.55〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 96 | Kobe Bryant / 2015 / Revolution Basketball #7 | 95.5〔81.18–109.83〕 | 230.49〔149.82–311.16〕 | 230.49–230.49 | 上调 | 1 |
| 97 | Los Angeles Lakers / 2009 / Panini Basketball / Team Checklist #290 | 155.0〔100.75–209.25〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 98 | Hakeem Olajuwon / 1998 / Encore #30 | 2.01〔1.31–2.71〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 99 | Jason Kidd / 1997 / UD3 / Starstruck #28 | 4.0〔2.6–5.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 100 | Shaquille O'Neal / 2025 / Topps Chrome / Fanatical #FAN-16 | 150.0〔127.5–215.67〕 | 40.60〔34.51–78.20〕 | 40.60–68.00 | 下调 | 2 |
| 101 | Trae Young / 2024 / Topps Chrome / Fanatical #FAN-12 | 50.0〔42.5–125.0〕 | 45.60〔29.64–61.56〕 | 45.60–45.60 | 下调 | 1 |
| 103 | Kobe Bryant / 2008 / SP Authentic #89 | 26.01〔19.99–71.26〕 | 26.01〔16.91–35.11〕 | 26.01–26.01 | 维持 | 1 |
| 104 | Elton Brand / 2000 / Upper Deck Reserve / Principal Powers #PP4 | 2.0〔1.3–2.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 105 | Stephen Curry / 2009 / Upper Deck Draft Edition #34 | 500.0〔425.0–575.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 106 | Bronny James Jr. / 2025 / Topps Chrome Updates Basketball / Purple #103 /75 | 80.0〔52.0–108.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 107 | Joel Embiid / 2025 / Topps Chrome / Serenity #SE-8 | 8.09〔5.26–10.92〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 108 | Kobe Bryant / 2005 / SP Authentic #38 | 35.0〔22.75–47.25〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 109 | MAGIC JOHNSON / 2025 / Topps Chrome Updates Basketball / Stratospheric Stars / Blue #ST-22 /150 | 27.0〔17.55–36.45〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 110 | Joan Beringer / 2025 / Topps Chrome Updates Basketball / Aqua Wave #167 /199 | 10.0〔8.5–60.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 111 | Jaylen Brown / 2024 / National Treasures / Colossal Materials #CM-JBR /99 | 75.01〔46.07–86.26〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 112 | Alonzo Mourning / 1998 / UD Choice / Starquest / Blue #SQ14 | 2.0〔1.7–5.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 113 | TYRESE PROCTOR / 2025 / Topps Chrome #200 /199 | 21.0〔9.0–24.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 114 | Stephon Marbury / 1998 / UD Choice / Starquest / Blue #SQ16 | 4.03〔3.4–4.63〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 115 | Jaylen Brown / 2025 / Topps Chrome / Captains #SC-14 | 302.24〔235.0–347.58〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 116 | Chauncey Billups / 1998 / UD Choice / Starquest / Blue #SQ26 | 2.2〔1.0–2.53〕 | 1.00〔0.85–1.15〕 | 1.00–1.00 | 下调 | 3 |
| 117 | Trae Young / 2025 / NBA Hoops / Oasis #VL-FLASH | 168.88〔122.0–235.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 118 | Julius Randle / 2025 / Topps Chrome Updates Basketball / Captains #SC-15 | 98.88〔66.1–122.8〕 | 66.10〔56.19–113.71〕 | 66.10–98.88 | 下调 | 2 |
| 119 | Tracy McGrady / 2000 / Upper Deck Hardcourt #53 | 8.0〔6.8–9.2〕 | 8.00〔1.00–12.00〕 | 1.00–12.00 | 维持 | 3 |
| 120 | DERIK QUEEN / 2025 / Topps Chrome Updates Basketball / Topps Chrome Autographs / Silver Refractor #TCA-DQ | 69.1〔58.74–86.25〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 121 | Donovan Mitchell / 2025 / NBA Hoops / Oasis | 142.0〔100.0–163.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 122 | DEVIN VASSELL / 2023 / Mosaic #154 | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 123 | Keyonte George / 2025 / Topps Chrome / AZZ #AZZ /399 | 53.0〔24.45–60.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 124 | JASON TERRY / 2001 / Upper Deck Hardcourt / UD Game Floor #JT | 30.0〔18.28–34.5〕 | 30.00〔18.28–34.50〕 | 21.50–30.00 | 维持 | 2 |
| 125 | Alex Sarr / 2025 / Topps Chrome Updates Basketball / Topps Chrome Autographs #TCA-AS | 50.0〔42.5–57.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 126 | SCOTTIE BARNES / 2024 / Topps Chrome / Blue /150 | 12.01〔7.06–13.81〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 127 | DENNIS RODMAN / 1996 / UD3 / Rookie Focus #31 | 23.88〔18.0–27.46〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 128 | Bobby Portis / 2024 / Topps Chrome #124 /275 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 129 | JOSH GIDDEY / 2025 / Topps Chrome #118 /275 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 130 | DENNIS RODMAN / 1996 / UD3 / Rookie Focus #31 | 23.88〔18.0–27.46〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 132 | Victor Wembanyama / 2023 / Mosaic / Thunder Road #8 | 127.0〔33.0–146.05〕 | 232.00〔150.80–313.20〕 | 232.00–232.00 | 上调 | 1 |
| 133 | Kobe Bryant / 2009 / Donruss / Team Checklists #290 | 49.81〔32.38–67.24〕 | 49.81〔32.38–67.24〕 | 49.81–49.81 | 维持 | 1 |
| 134 | VJ EDGE COMBE / 2025 / Topps Chrome #6 | 7.0〔5.95–9.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 136 | LUKA DONČIĆ / 2025 / NBA Hoops / Hoopnotic #HN-10 | 411.99〔161.0–473.79〕 | 415.00〔269.75–560.25〕 | 415.00–415.00 | 上调 | 1 |
| 137 | Victor Wembanyama / 2024 / Mosaic / Stare Masters #15 | 11.0〔9.35–16.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 138 | Patrick Ewing / 1996 / Showcase / H #194-27/H | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 139 | VJ EDGE COMBE / 2025 / Court Kings / Rookies III #167 | 51.82〔39.0–62.0〕 | 62.00〔28.90–71.30〕 | 34.00–62.00 | 上调 | 2 |
| 140 | JAMES HARDEN / 2025 / Topps Chrome / Stars #33 | 2.0〔1.7–3.0〕 | 2.00〔1.30–2.70〕 | 2.00–2.00 | 维持 | 1 |
| 141 | SHAEDON SHARPE / 2025 / Topps Chrome #106 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 142 | Kobe Bryant / 1999 / Masters of the Arts / Honorary Degree in Scoring #MA8 /12 | 45.0〔27.0–70.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 143 | Jaylen Brown / 2025 / Topps Chrome Update / Clutch Gene #CG-10 /99 | 48.0〔31.2–64.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 144 | Kobe Bryant / 1997 / Ultra / Career Highlights #61 | 71.0〔60.35–86.08〕 | 93.24〔79.25–107.23〕 | 38.00–95.01 | 上调 | 3 |
| 145 | Kobe Bryant / 2000 / MVP / World Jam #WJ1 | 71.89〔61.11–90.01〕 | 71.89〔61.11–92.00〕 | 71.89–80.00 | 维持 | 2 |
| 147 | ANDREW NEMBHARD / 2024 / Prizm #6 /249 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 148 | VJ EDGE COMBE / 2025 / NBA Hoops #244 | 4.02〔2.61–5.43〕 | 4.02〔1.70–4.62〕 | 2.00–4.02 | 维持 | 2 |
| 149 | Jaylen Brown / 2019 / Contenders Basketball / Semifinal Ticket #42 /149 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 150 | BEN SIMMONS / 2019 / Contenders Basketball / Semifinal Ticket #9 /149 | 67.11〔30.2–110.73〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 151 | LeBron James / 2024 / Topps Chrome #29 | 8.0〔4.4–11.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 152 | MARK JACKSON / 2019 / National Treasures Basketball / Signatures #S-MJK /25 | 65.0〔29.25–107.25〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 153 | Jaylen Brown / 2025 / Topps Chrome #LOHP5 | 2.0〔1.1–72.51〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 154 | Jaylen Brown / 2025 / Topps Chrome #35 | 55.0〔24.75–90.75〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 155 | CEDRIC COWARD / 2025 / Topps Chrome #161 | 7.0〔0.85–8.05〕 | 9.00〔7.65–10.35〕 | 9.00–9.00 | 上调 | 2 |
| 156 | JAMAL MURRAY / 2025 / Topps Chrome / Captains #SC-5 | 265.0〔225.25–304.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 157 | Dereck Lively II / 2024 / Totally Certified Basketball / Mirror Blue Camo Platinum #68 /35 | 8.41〔5.47–11.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 158 | VJ EDGE COMBE / 2024 / Flagship / Blue | 2.0〔1.2–3.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 159 | TYRESE MAXEY / 2020 / Mosaic / National Pride #259 | 12.0〔10.0–13.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 160 | Michael Jordan / 1997 / Metal Universe / Intimidation Nation / Champions #IN-1 | 414.0〔186.3–683.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 161 | Kon Knueppel / 2025 / NBA Hoops / Hard Wired #HW-24 | 9.0〔5.1–11.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 162 | BRANDON MILLER / 2023 / Court Kings / Rookies I | 6.0〔5.0–6.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 163 | ANTHONY EDWARDS / 2020 / Prizm / Instant Impact #1 | 4.0〔3.0–7.0〕 | 7.00〔4.00–9.13〕 | 3.00–9.13 | 上调 | 4 |
| 164 | ACE BAILEY / 2025 / Topps Chrome / Clutch City / Refractor #YQ-5 | 6.0〔5.1–12.54〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 165 | ANTHONY EDWARDS / 2020 / Prizm / Emergent #3 | 3.0〔1.0–9.0〕 | 6.50〔1.00–10.80〕 | 1.00–13.00 | 上调 | 6 |
| 166 | STEPHON CASTLE / 2025 / Topps Chrome #82 | 4.1〔2.67–5.54〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 167 | STEPHON CASTLE / 2025 / Topps Chrome #82 | 13.3〔8.65–17.96〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 168 | LUKA DONČIĆ / 2024 / Totally Certified / On Target #23 /265 | 6.14〔2.1–17.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 170 | Zach LaVine / 2015 / Revolution Basketball / Green Splash #12 | 208.0〔176.8–239.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 171 | Luka Doncic / 2023 / Mosaic Basketball / Give And Go / Mosaic Pink Fluorescent #11 /10 | 602.0〔391.3–812.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 172 | Michael Jordan / 1990 / AW4 / MVP #AW4 | 82.0〔53.3–110.7〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 173 | Dirk Nowitzki / 2025 / Signature Series Basketball #97 /25 | 70.6〔22.78–81.19〕 | 70.60〔22.78–81.19〕 | 26.80–70.60 | 维持 | 2 |
| 174 | Jason Kidd / 2022 / Prizm / Signatures #SIG-JKD /25 | 110.0〔49.5–181.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 175 | STEPHON CASTLE / 2024 / Prizm / Black #96 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 176 | VJ EDGE COMBE / 2025 / NBA Hoops / Block by Block #BYB-3 | 200.0〔160.0–250.06〕 | 200.00〔170.00–287.57〕 | 200.00–250.06 | 维持 | 2 |
| 177 | LUKA DONČIĆ / 2024 / Origins Basketball / Cosmic Storm / Cosmic Storm #18 /25 | 24.13〔3.3–34.99〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 178 | Victor Wembanyama / 2023 / NBA Hoops / Anti Gravity #70 | 40.0〔32.0–46.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 179 | MATAS BUZELIS / 2024 / Prizm #252 | 1.0〔0.85–1.15〕 | 1.00〔0.85–2.30〕 | 1.00–2.00 | 维持 | 2 |
| 180 | WALKER KESSLER / 2022 / Prizm #234 | 1.0〔0.85–3.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 181 | WALKER KESSLER / 2022 / Prizm #234 /19 | 1.0〔0.85–3.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 182 | Kon Knueppel / 2024 / Midnight #64 | 200.34〔130.22–270.46〕 | 262.00〔201.00–384.00〕 | 201.00–384.00 | 上调 | 3 |
| 183 | Kobe Bryant / 2000 / Pure Basketball #PB4 | 110.0〔71.5–148.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 184 | Dirk Nowitzki / 1998 / Brilliants / H #109B | 16.0〔13.6–18.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 185 | MATAS BUZELIS / 2024 / Prizm #252 | 1.0〔0.85–2.0〕 | 1.00〔0.85–2.30〕 | 1.00–2.00 | 维持 | 2 |
| 186 | Dirk Nowitzki / 2023 / PLAYER OF THE DAY BASKETBALL / PLAYER OF THE DAY HIGHLIGHT #86 /99 | 4.58〔2.98–6.18〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 189 | Tracy McGrady / 2019 / Prizm #26 | 10.01〔6.51–13.51〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 190 | Dirk Nowitzki / 2020 / PLAYER OF THE DAY BASKETBALL #MMC-DN /50 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 191 | J.J. BAREA / 2019 / Opulence Basketball / Luxurious #LA-JJ /49 | 43.0〔27.95–58.05〕 | 43.00〔27.95–58.05〕 | 43.00–43.00 | 维持 | 1 |
| 195 | Magic Johnson, Kareem Abdul-Jabbar / 2022 / Contenders Optic / Legendary Tandems #9 | 11.2〔7.28–15.12〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 196 | Jason Kidd / 2021 / National Treasures / Timeless Talents #TTS-JKD | 251.0〔150.6–351.4〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 200 | Metta World Peace / 2025 / TOPPS HOOPS BASKETBALL / HOOPS 1989 SIGNATURES #89S-MW | 37.0〔24.05–49.95〕 | 37.00〔24.05–49.95〕 | 37.00–37.00 | 维持 | 1 |
| 203 | LeBron James / 2020 / Mosaic / Finals Mvps #297 | 67.0〔5.0–77.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 204 | Vin Baker / 1995 / ProFiles / PF-4 #PF-4 | 4.0〔0.7–5.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 205 | Dirk Nowitzki / 2016 / Complete Basketball / HOME #7 | 36.01〔11.01–84.11〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 206 | Kobe Bryant / 2016 / NBA Hoops / Legend Edition #24 | 25.0〔10.26–35.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 208 | KYRIE IRVING / 2023 / Photogenic Basketball / Championship Feels #7 | 95.1〔80.84–109.37〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 210 | LeBron James / 2003 / Upper Deck / Rookie of the Year #LJ44 | 71.89〔61.11–94.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 211 | Amen Thompson / 2023 / Photogenic Basketball / Rookie Focus #7 | 17.0〔14.45–27.13〕 | 27.13〔8.61–31.20〕 | 10.13–27.13 | 上调 | 2 |
| 212 | LeBron James / 2022 / Court Kings / Purple #35 | 12.0〔6.3–18.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 213 | AUSAR THOMPSON / 2023 / Photogenic Basketball / Rookie Focus #6 | 8.2〔1.0–11.0〕 | 8.20〔1.00–11.00〕 | 1.00–16.90 | 维持 | 4 |
| 215 | TIDJANE SALAÜN / 2024 / Revolution Basketball / Vortex / Astro #5 /299 | 3.1〔1.4–5.12〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 216 | Jason Kidd / 1997 / Metal Universe / Metal #47 | 4.5〔2.7–7.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 217 | Jaylen Brown / 2025 / NBA Hoops / Checkmate #C-27 | 83.0〔69.0–96.0〕 | 83.00〔53.95–112.05〕 | 83.00–83.00 | 维持 | 1 |
| 220 | BRANDON MILLER / 2023 / NBA Hoops / City Edition / Premium Stock #12 | 3.0〔2.55–4.6〕 | 3.00〔1.95–4.05〕 | 3.00–3.00 | 维持 | 1 |
| 221 | SCOTTIE BARNES / 2024 / NBA Hoops / Green #101 | 2.0〔0.9–3.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 224 | KEVIN DURANT / 2012 / Marquee #15 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 225 | AUSAR THOMPSON / 2023 / Prizm Draft Picks / Draft Picks #17 | 49.0〔31.85–66.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 226 | Stephen Curry / 2023 / Prizm Draft Picks / Brilliance #30 | 15.1〔6.82–17.37〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 227 | Victor Wembanyama / 2023 / Mosaic / Thunder Road #8 | 127.0〔37.0–146.05〕 | 33.00〔21.45–44.55〕 | 33.00–33.00 | 下调 | 1 |
| 228 | Jamir Watkins / 2026 / Signature Class Basketball / Rookie Class | 2.0〔1.2–3.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 229 | Jamir Watkins / 2026 / Signature Class Basketball / Rookie Class Autographs Card #2725 | 17.0〔10.2–23.8〕 | 18.11〔11.77–24.45〕 | 18.11–18.11 | 上调 | 1 |
| 230 | Bobby Portis / 2025 / Topps Chrome / NBA Record #124 /275 | 3.0〔2.55–3.45〕 | 2.05〔1.74–2.36〕 | 2.00–3.01 | 下调 | 3 |
| 231 | AUSAR THOMPSON / 2023 / Prizm Draft Picks / Draft Picks #33 | 49.0〔31.85–66.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 232 | Stephen Curry / 2023 / Prizm Draft Picks / Brilliance #30 | 15.1〔6.82–17.37〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 233 | KEVIN DURANT / 2012 / Marquee #75 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 234 | Kobe Bryant / 2015 / Revolution / Purple #38 | 131.0〔6.3–195.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 235 | NATE AMENT / 2025 / Topps Chrome / Winning Tags #WT-NA | 129.0〔109.65–177.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 236 | Anthony Davis / 2025 / NBA Hoops / Bounce House /50 | 103.0〔31.96–118.45〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 237 | Hakeem Olajuwon / 1999 / Encore / Game Dated #30 | 2.01〔1.31–2.71〕 | 2.01〔1.31–2.71〕 | 2.01–2.01 | 维持 | 1 |
| 238 | Kobe Bryant / 1998 / Metal Universe / Metal/2 #2 | 160.0〔55.37–256.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 239 | Shaquille O'Neal / 2025 / Topps Chrome / Fanatical #FAN-16 | 132.0〔111.0–153.74〕 | 132.00〔72.26–151.80〕 | 85.01–132.00 | 维持 | 2 |
| 240 | VJ EDGE COMBE / 2025 / NBA Hoops / Bounce House | 12.0〔10.2–14.71〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 241 | Jason Kidd / 1997 / Starstruck #28 | 6.05〔1.41–14.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 242 | Paolo Banchero / 2025 / NBA Hoops / Oasis | 156.0〔101.0–352.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 243 | Jayson Tatum / 2025 / NBA Hoops / Bounce House /50 | 2.0〔0.55–2.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 245 | Dirk Nowitzki / 1998 / Showcase / Some Mavericks' 1st Round Picks | 55.0〔15.48–78.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 246 | Kobe Bryant / 1998 / Victory / Fly 2 Kobe #297 | 10.0〔4.5–16.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 247 | Kobe Bryant / 2000 / Victory / Fly 2 Kobe #295 | 20.13〔17.11–23.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 248 | Kobe Bryant / 2000 / Focus #62 | 26.0〔22.1–29.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 249 | Jaylen Brown / 2025 / Topps Chrome Update / Clutch Gene #CG-10 /150 | 1.0〔0.6–1.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 250 | Kobe Bryant / 1998 / SP Authentic #44 | 57.11〔48.54–65.68〕 | 57.11〔48.54–65.68〕 | 45.10–57.11 | 维持 | 3 |
| 251 | Kobe Bryant / 1998 / Upper Deck / Bryant-8 #58 | 26.0〔22.0–29.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 252 | Jaylen Brown / 2016 / Donruss Basketball #7 | 8.16〔2.1–19.08〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 253 | LeBron James / 2025 / Topps Chrome / Inspirational | 12.2〔10.37–14.03〕 | 12.25〔10.41–14.09〕 | 6.01–16.00 | 上调 | 8 |
| 254 | Kobe Bryant / 1998 / Collector's Choice #M13 | 95.0〔80.75–109.25〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 255 | Jaylen Brown / 2025 / Topps Chrome / Fortune 15 / F15-2 | 1.0〔0.85–3.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 256 | LeBron James / 2025 / Topps Chrome | 248.0〔111.6–409.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 257 | LUKA DONČIĆ / 2025 / Topps Chrome / LAKERS / X | 13.01〔5.0–16.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 258 | KYRIE IRVING / 2015 / NBA #127 | 5.08〔2.1–9.27〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 259 | LeBron James / 2018 / Winter / W-3 #W-3 | 16.0〔7.2–26.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 261 | stephon marbury / 2003 / Showcase / Honored #71 | 11.2〔5.04–18.48〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 262 | Jaylen Brown / 2021 / Court Kings #8 /25 | 55.0〔46.75–63.25〕 | 56.00〔46.75–64.40〕 | 55.00–56.00 | 上调 | 2 |
| 263 | Asa Newell / 2025 / NBA Hoops / Bounce House /50 | 93.0〔17.85–106.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 267 | Bam Adebayo / 2025 / NBA Hoops / Oasis | 90.6〔77.01–138.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 269 | Tyrese Haliburton / 2025 / NBA Hoops / Bounce House /50 | 3.0〔1.46–4.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 270 | Jalen Johnson / 2025 / NBA Hoops / Bounce House | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 271 | Derek Dixon / 2025 / Bowman Chrome / Chrome Prospect Autograph Card /50 | 47.0〔6.0–103.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 272 | Giannis Antetokounmpo / 2025 / NBA Hoops / Bounce House /50 | 2.0〔1.2–2.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 273 | EGOR DËMIN / 2025 / Bowman Chrome #BRR-8 | 3.0〔1.35–4.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 274 | CARTER BRYANT / 2025 / Topps Chrome / X #164 | 9.03〔5.87–12.19〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 275 | LeBron James / 2020 / Prizm #1 | 55.0〔35.75–74.25〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 276 | LaMelo Ball / 2020 / Prizm / Emergent #23 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 291 | Michael Jordan / 1990 / NBA Hoops / Chicago Bulls / Gold #358 | 55.0〔13.31–104.0〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 292 | COOPER FLAGG / 2025 / Chrome / MEGA ROOKIES / COPPER | 261.2〔189.0–300.38〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 293 | Kobe Bryant / 1998 / UD Choice / Starquest #SQ13 | 295.0〔250.75–664.55〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 294 | VJ EDGE COMBE / 2025 / Topps Now / KIA NBA ALL-ROOKIE FIRST TEAM SELECTION #6 | 22.0〔18.7–25.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 295 | SCOTTIE BARNES / 2025 / Midnight /5 | 1.0〔0.6–10.17〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 296 | Luka Doncic / 2018 / Prizm #280 | 333.0〔216.45–449.55〕 | 333.00〔216.45–449.55〕 | 333.00–333.00 | 维持 | 1 |
| 297 | Dirk Nowitzki / 1998 / Brilliants / Rookie '98-'99 #109B | 16.0〔11.0–18.4〕 | 15.10〔9.82–20.39〕 | 15.10–15.10 | 下调 | 1 |
| 298 | Kon Knueppel / 2025 / Midnight #64 | 353.0〔300.05–405.95〕 | 369.00〔313.65–424.35〕 | 353.00–384.00 | 上调 | 4 |
| 299 | Victor Wembanyama / 2023 / Prizm #136 | 315.01〔267.76–362.26〕 | 315.60〔268.26–415.00〕 | 315.00–415.00 | 上调 | 3 |
| 302 | VJ EDGE COMBE / 2025 / Midnight / Dream Land | 1013.89〔861.81–1207.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 304 | VJ EDGE COMBE / 2024 / Midnight / Dream Land | 133.78〔80.27–187.29〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 305 | Victor Wembanyama / 2023 / Prizm #136 | 315.01〔267.76–362.26〕 | 411.00〔315.60–472.65〕 | 315.00–415.00 | 上调 | 6 |
| 308 | VINCE CARTER / 2019 / Prizm / Green #15 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 309 | VJ EDGE COMBE / 2025 / Bowman Chrome / Greatness Loading #GL-3 | 22.0〔4.1–25.3〕 | 4.10〔2.67–5.54〕 | 4.10–4.10 | 下调 | 1 |
| 313 | Jason Kidd / 2024 / National Treasures / Timeless Talents #TTS-JKD /35 | 205.0〔112.75–600.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 314 | Jason Kidd / 2022 / Impeccable / Immortal Ink #II-JKD /35 | 270.01〔229.51–400.66〕 | 270.01〔175.51–364.51〕 | 270.01–270.01 | 维持 | 1 |
| 315 | Murad / 2009 / T-51 Murad #T51A-CM | 23.88〔13.3–61.1〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 316 | 夏思凝 / 2025 / 中国体育冠军系列明星卡 / 锦绣 #S-144 /49 | 2.0〔0.7–3.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 320 | Michael Jordan / 1990 / Jordan Legacy / Jordan 23 Legacy #2 | 22.3〔11.2–45.51〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 322 | Dirk Nowitzki / 2014 / NBA HOOPS / HIGH HONORS #14 BEM 9.0 | 6.0〔2.7–9.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 323 | Dirk Nowitzki / 2025 / Flagship / X #252 | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 324 | LeBron James / 2024 / Top Class / TC #269 | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 325 | TIM DUNCAN / 2002 / Stadium Club #CFLI3 | 13.1〔8.52–17.69〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 326 | stephon marbury / 1997 / Rookie Rewind #S019 | 4.0〔0.7–5.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 328 | Dirk Nowitzki / 2020 / Mosaic / Finals Mvps #298 | 3.01〔1.96–4.06〕 | 3.01〔1.96–4.06〕 | 3.01–3.01 | 维持 | 1 |
| 330 | Dirk Nowitzki / 2004 / Diamond Prosigs Collection / Mystique #16 | 9.2〔4.41–14.56〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 331 | Jason Kidd / 2010 / Threads #10 | 61.5〔39.98–83.03〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 332 | VINCE CARTER / 1999 / Mystique / DIAL 1 #15 | 35.4〔23.38–40.71〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 333 | Kobe Bryant / 2004 / SP Authentic #35 | 25.0〔16.25–33.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 334 | AUSAR THOMPSON / 2023 / Photogenic Basketball / Rookie Focus #6 | 8.2〔1.0–11.0〕 | 8.20〔1.00–11.00〕 | 1.00–16.90 | 维持 | 4 |
| 335 | Michael Jordan / 1991 / League Leaders #220 | 12.0〔10.0–13.8〕 | 12.00〔10.00–13.80〕 | 10.00–13.00 | 维持 | 3 |
| 337 | EGOR DËMIN / 2025 / Topps Chrome / Next Stop Signatures / Refraactor | 73.0〔43.8–124.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 338 | Kobe Bryant / 2001 / Upper Deck / VAC #74 | 51.0〔33.15–68.85〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 339 | LeBron James / 2024 / Select Basketball / Blue Prizm #54 | 5.0〔4.25–9.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 340 | VINCE CARTER / 1999 / Century Legends / Generations #1008 | 19.0〔12.35–25.65〕 | 19.00〔12.35–25.65〕 | 19.00–19.00 | 维持 | 1 |
| 341 | Jayson Tatum / 2025 / NBA Hoops / Oasis | 438.66〔372.86–504.85〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 342 | Kobe Bryant / 1998 / Metal Universe | 160.4〔77.7–367.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 343 | LeBron James / 2025 / NBA Hoops / Hard Wired #PLAYER | 12.0〔9.9–13.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 344 | Michael Jordan / 2022 / Goodwin Champions / Goudey #GP1 | 4.0〔3.0–7.0〕 | 3.00〔2.55–7.00〕 | 3.00–7.00 | 下调 | 3 |
| 345 | Michael Jordan / 1994 / Rare Air / decade of DOMINANCE #58 | 30.0〔11.9–34.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 346 | Michael Jordan / 1999 / MVP #219 | 17.55〔12.1–20.18〕 | 17.55〔9.35–20.18〕 | 11.00–17.55 | 维持 | 2 |
| 347 | Jerry Stackhouse / 1998 / Metal Universe / Metal/2 #47 | 4.0〔1.8–6.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 349 | Shai Gilgeous-Alexander / 2025 / Topps Basketball / Daily Dribble | 4.0〔3.4–4.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 350 | Stephen Curry / 2024 / Mosaic / Blue #30 | 17.05〔10.23–41.34〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 351 | BRANDON MILLER / 2023 / NBA Hoops / City Edition #12 | 3.0〔2.55–4.6〕 | 3.00〔2.55–4.60〕 | 3.00–4.60 | 维持 | 3 |
| 352 | Amen Thompson / 2023 / 2023-24 Panini – Premium Stock Basketball / Anti Gravity #16 /199 | 1.0〔0.6–1.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 354 | EVAN MOBLEY / 2024 / NBA Hoops / Blue #82 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 355 | Amen Thompson / 2023 / Premium Stock Basketball / Anti Gravity #16 /199 | 9.58〔0.7–58.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 356 | Shai Gilgeous-Alexander / 2024 / Topps Basketball / Daily Dribble | 4.0〔3.4–4.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 357 | Gradey Dick / 2024 / Midnight #40 /99 | 5.0〔3.25–6.75〕 | 5.00〔3.25–6.75〕 | 5.00–5.00 | 维持 | 1 |
| 358 | Allen Iverson / 1997 / Metal Universe / World Balloon Maker / Metal #20 | 27.99〔11.0–32.19〕 | 7.50〔6.38–12.65〕 | 7.50–11.00 | 下调 | 2 |
| 359 | Allen Iverson / 1997 / Metal Universe #26 | 11.2〔7.28–15.12〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 360 | Dirk Nowitzki / 2023 / Court Kings / Water Color #20 | 23.66〔6.0–30.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 361 | Kobe Bryant / 2018 / Donruss Basketball / All Heart #19 | 34.0〔28.9–39.1〕 | 35.00〔29.75–40.25〕 | 32.01–35.00 | 上调 | 5 |
| 362 | Bam Adebayo / 2025 / NBA Hoops / Oasis | 90.6〔77.01–138.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 364 | Reed Sheppard / 2025 / Flagship / Gold #178 /2025 | 3.05〔2.55–3.51〕 | 3.05〔2.55–3.51〕 | 3.00–3.05 | 维持 | 2 |
| 365 | Dirk Nowitzki / 2003 / Showcase / Legacy Collection #95 /125 | 48.88〔31.77–65.99〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 366 | Nique Clifford / 2025 / Bowman Chrome / Greatness Loading / Silver /25 | 1.0〔0.6–1.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 367 | Shai Gilgeous-Alexander / 2025 / Topps Basketball / Daily Dribble #DD-2 | 4.0〔3.4–7.01〕 | 4.00〔3.40–7.01〕 | 2.01–7.99 | 维持 | 5 |
| 368 | LeBron James / 2023 / Hoops Basketball #290 | 1.0〔0.85–5.0〕 | 1.00〔0.85–1.15〕 | 1.00–1.00 | 维持 | 2 |
| 369 | DARRYN PETERSON / 2025 / Topps Chrome / McDonald's All American / Silver | 5.1〔4.0–6.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 370 | LeBron James / 2023 / Donruss Basketball / Unleashed #1 | 6.0〔5.05–6.9〕 | 5.20〔3.51–8.00〕 | 3.51–8.00 | 下调 | 5 |
| 371 | VJ EDGE COMBE / 2024 / Topps Chrome / GO TIME | 6.0〔5.1–6.9〕 | 5.00〔4.25–8.00〕 | 1.00–8.00 | 下调 | 5 |
| 373 | Ray Allen / 1997 / Ultra / Dare to Compare #30 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 374 | Nickeil Alexander-Walker / 2021 / Spectra Basketball / Catalysts Signatures #CAS-NAW /99 | 35.0〔20.85–51.66〕 | 23.88〔15.52–32.24〕 | 23.88–23.88 | 下调 | 1 |
| 375 | OLIVIER-MAXENCE PROSPER / 2023 / Prizm / Rookie Signatures / Blue #RSI-OMP | 12.01〔6.61–18.85〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 376 | GRANT RILLER / 2019 / Contenders / Draft Picks #1 | 8.01〔3.6–13.22〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 377 | IGNAS BRAZDEIKIS / 2019 / Contenders Basketball / The Finals Ticket #1273 | 18.0〔10.8–29.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 378 | Romeo Weems / 2020 / Chronicles Donruss Draft Picks / Rated Rookie #RR-RWE /49 | 6.0〔5.0–6.9〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 379 | DANIEL OTURU / 2020 / Prizm / Rookie Signatures #2 | 14.0〔8.52–16.1〕 | 14.00〔9.10–18.90〕 | 14.00–14.00 | 维持 | 1 |
| 380 | OUSMANE DIENG / 2024 / Donruss Optic / Signature Series #SS-OUS | 14.0〔11.9–18.0〕 | 17.13〔11.13–23.13〕 | 17.13–17.13 | 上调 | 1 |
| 382 | DAVION MITCHELL / 2021 / Court Kings / Rookie Expression #REM-DVM | 1.0〔0.6–3.9〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 383 | LeBron James / 2024 / Revolution Basketball #31 | 11.0〔7.15–14.85〕 | 6.00〔0.85–6.90〕 | 1.00–6.00 | 下调 | 2 |
| 386 | TJ LEAF / 2017 / Spectra Basketball / Rising Stars Signatures #RS-TJL /199 | 26.0〔11.7–42.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 387 | Kyle Anderson / 2014 / Donruss Basketball / Rookie Autographs Die-Cuts #31 /49 | 34.0〔20.4–47.6〕 | 32.00〔20.80–43.20〕 | 32.00–32.00 | 下调 | 1 |
| 388 | LeBron James / 2022 / Goodwin Champions / Playing Cards #KING-DIAMOND | 6.1〔3.97–8.24〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 392 | LeBron James / 2023 / Contenders Basketball / Supernatural #17 | 30.01〔25.51–36.74〕 | 11.74〔9.98–27.00〕 | 9.10–27.00 | 下调 | 3 |
| 395 | DERRICK WHITE / 2025 / Topps Chrome / Purple /75 | 22.07〔15.45–28.69〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 396 | BARON DAVIS / 1999 / FLAIR SHOWCASE / NEXT #19 AP 8.0 | 3.2〔1.44–5.28〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 399 | Egor Demin / 2025 / Topps Chrome Updates / Clutch City / Purple Geometric Refractor #YQ-11 /75 | 22.2〔18.87–37.95〕 | 证据不足 | 不足 | 维持不足 | 0 |
| 400 | Jonathan Kuminga / 2021 / Donruss Optic / Rated Rookie #190 | 3.0〔1.95–4.05〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 408 | LaMelo Ball / 2020 / Mosaic #202 | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 409 | NIKOLA JOKIĆ / 2024 / Prizm / TALISMEN #7 /49 | 2.0〔0.7–10.4〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 410 | TIM DUNCAN / 2020 / Court Kings / Artistry in Motion #16 /25 | 177.0〔115.05–238.95〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 428 | Steve Nash / 1997 / Premium #25 | 20.01〔9.0–33.02〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 429 | JAMAL MASHBURN / 2003 / MVP #111 | 2.0〔1.2–2.8〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 430 | Kevin Garnett / 2004 / Bowman #21 | 9.0〔4.05–14.85〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 431 | TY LAWSON / 2012 / Brilliance Basketball / Magic Numbers #5 | 2.0〔1.3–2.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 437 | DERIK QUEEN / 2025 / Prizm / Brilliance #12 /249 | 1.0〔0.55–1.45〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 439 | MICHAEL REDD / 2025 / Prizm / Red /299 | 15.0〔6.75–24.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 440 | RICHARD HAMILTON / 2025 / Prizm #178 /299 | 1.0〔0.85–2.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 442 | Donovan Mitchell, Ja Morant / 2021 / Donruss / Duos #5 | 3.01〔1.35–4.97〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 443 | Jason Kidd, Jason Terry, Dirk Nowitzki / 2017 / Status / Factions /199 | 11.1〔6.66–16.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 444 | Damian Lillard, C.J. McCollum, Jusuf Nurkic / 2017 / Status / Factions #1 | 4.0〔2.6–5.4〕 | 4.00〔2.60–5.40〕 | 4.00–4.00 | 维持 | 1 |
| 445 | TIM DUNCAN / 1999 / Showcase / Flair Showcase Power #PA5 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 446 | Michael Finley / 1996 / SPx #38 | 4.0〔0.7–11.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 448 | DYLAN HARPER / 2025 / NBA Hoops / Next Episode / NE-2 | 15.38〔13.07–35.44〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 449 | Gradey Dick / 2023 / Donruss Elite Basketball / Craftsmen #29 | 3.0〔1.16–4.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 450 | MARCUS SASSER / 2023 / Court Kings #12 | 3.0〔1.8–5.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 451 | DERIK QUEEN / 2025 / Topps Chrome / GO TIME #GT-15 /75 | 3.0〔0.7–6.5〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 452 | PAUL ATKINSON JR. / 2022 / Prizm Draft Picks / Draft Picks #DP-PAJ /199 | 3.0〔1.8–4.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 453 | PEJA STOJAKOVIC / 2024 / Prizm / Sensational Signatures #SS-PSK | 30.04〔19.53–40.55〕 | 30.04〔19.53–40.55〕 | 30.04–30.04 | 维持 | 1 |
| 454 | VJ EDGE COMBE / 2025 / NBA Hoops / Bounce House #BH-18 /50 | 11.0〔5.6–15.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 456 | Victor Wembanyama / 2023 / 2023-24 Panini - Phoenix Basketball #256 | 35.4〔30.09–40.71〕 | 35.00〔29.75–40.25〕 | 3.00–35.00 | 下调 | 4 |
| 458 | SHAEDON SHARPE / 2025 / Topps Chrome / Refractor / Silver #106 | 1.0〔0.65–1.35〕 | 1.00〔0.65–1.35〕 | 1.00–1.00 | 维持 | 1 |
| 459 | JEREMIAH FEARS / 2025 / Topps Chrome / Crescent City / Gold /50 | 576.0〔374.4–777.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 461 | ANFERNEE SIMONS / 2024 / Topps Chrome | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 462 | JOSH HART / 2025 / Topps Chrome / Refactor / Refractor | 2.0〔0.9–3.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 465 | ALIJAH MARTIN / 2025 / Topps Chrome | 2.0〔1.1–2.9〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 466 | CEDRIC COWARD / 2025 / Topps Chrome / JULIUS GRIFFIN | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 467 | ANTONIO REEVES / 2024 / Revolution Basketball / Lightning #127 /175 | 3.0〔1.95–4.05〕 | 3.00〔1.95–4.05〕 | 3.00–3.00 | 维持 | 1 |
| 468 | Jamir Watkins / 2025 / Topps Chrome / Wizards | 1.0〔0.85–1.15〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 470 | RJ BARRETT / 2025 / Topps Chrome #73 | 1.0〔0.55–9.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 471 | Alex Sarr / 2025 / Topps Chrome | 3.0〔0.7–8.68〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 474 | SHELTON HENDERSON / 2025 / Bowman Chrome / Chrome Prospect Autograph Card /150 | 3.01〔2.56–22.0〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 475 | DYLAN HARPER / 2025 / Topps Chrome / X-Fractor | 131.0〔111.35–395.01〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 476 | TIDJANE SALAÜN / 2024 / Court Kings / Masterstrokes #MS-SAL /75 | 22.0〔15.8–25.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 477 | Paolo Banchero / 2025 / Topps Chrome / Magic /47 | 1.0〔0.45–1.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 478 | DEVIN VASSELL / 2022 / Photogenic / Mosaic #55 /75 | 3.0〔1.65–4.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 480 | Stephen Curry / 2024 / Donruss Optic / Express #13 | 7.08〔3.1–13.0〕 | 7.08〔3.10–13.00〕 | 3.10–14.00 | 维持 | 4 |
| 482 | Kon Knueppel / 2025 / NBA Hoops / Bounce House #BH-19 /50 | 428.0〔363.8–603.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 483 | Collin Murray-Boyles / 2025 / Chrome / Rockstar Rookies #RR-9 | 1.0〔0.65–1.35〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 484 | VJ EDGE COMBE / 2025 / Prizm / Brilliance /249 | 5.0〔4.25–85.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 485 | DYLAN HARPER / 2025 / Topps Chrome #CMP1 | 17.0〔14.45–19.55〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 486 | ACE BAILEY / 2025 / Topps Chrome / Utah Jazz Edition New /75 | 463.8〔378.34–533.37〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 490 | TYRESE MAXEY / 2025 / Bowman Chrome / Veterans #BCV-66 | 1.0〔0.55–8.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 491 | NIKOLA JOKIĆ / 2025 / NBA Hoops / Hoopers / H-1 /75 | 2.0〔0.9–3.3〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 495 | JAVON SMALL / 2025 / Chrome Update Basketball / Rookie Autograph Card /299 | 39.0〔19.45–44.85〕 | 39.00〔19.45–44.85〕 | 22.88–39.00 | 维持 | 2 |
| 496 | JAVON SMALL / 2025 / Topps Chrome Update Basketball / Rookie Autograph Card #RA-JSM /299 | 35.0〔27.2–40.25〕 | 35.00〔22.88–40.25〕 | 22.88–35.00 | 维持 | 3 |
| 497 | Doctor Strange / 2023 / Marvel Avengers #110 | 7.83〔3.52–12.92〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 498 | Khaman Maluach / 2025 / Topps Inception / Rookie Jumbo Relic Autograph #RJ-KM | 182.0〔154.7–281.0〕 | 182.00〔154.70–281.00〕 | 169.00–423.00 | 维持 | 4 |
| 499 | Michael Jordan / 2022 / GOODWIN CHAMPIONS / GOUDEY #G1 | 3.0〔2.55–7.0〕 | 3.00〔2.55–7.00〕 | 1.00–7.00 | 维持 | 4 |
| 500 | Tre Johnson III / 2025 / Topps Chrome #SE-13 | 4.0〔2.0–4.6〕 | 2.00〔1.70–2.30〕 | 2.00–2.00 | 下调 | 2 |
| 501 | KYRIE IRVING / 2025 / NBA Hoops / Oasis | 104.0〔88.4–204.7〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 503 | LeBron James / 2022 / Donruss Elite Basketball / Hyper Red and Green #122 | 11.1〔7.22–14.99〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 504 | Chris Paul / 2021 / HOOPS BASKETBALL / SLAM #SLAM #145 | 15.0〔12.75–19.0〕 | 14.00〔11.90–19.00〕 | 3.00–20.00 | 下调 | 5 |
| 505 | Shaquille O'Neal / 1996 / Gallery / The Masters #1 | 28.0〔12.6–46.2〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 506 | Michael Jordan / 1997 / Rare Air / Out Takes #53 | 18.01〔11.9–20.71〕 | 18.01〔11.90–20.71〕 | 14.00–18.01 | 维持 | 2 |
| 507 | Quentin Grimes / 2021 / Onyx Vintage Basketball / Onyx Authenticated Autograph / Red /22 | 4.0〔1.8–6.6〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 508 | Michael Jordan / 1994 / Rare Air #15 | 6.0〔3.9–8.1〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 509 | LEBRON JAMES / CARMELO ANTHONY / 2004 / Rivals #29 | 45.0〔29.25–60.75〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 510 | LeBron James / Kyrie Irving / 2014 / NBA / Double Trouble #2 | 23.38〔10.52–38.58〕 | 证据不足 | 不足 | 转证据不足 | 0 |
| 511 | MARK WILLIAMS / 2022 / Revolution Basketball / Cubic #104 /50 | 2.0〔1.3–2.7〕 | 2.00〔1.30–2.70〕 | 2.00–2.00 | 维持 | 1 |
| 512 | LUKA DONČIĆ / 2024 / NBA Hoops / Nobility #15 | 5.0〔3.5–5.75〕 | 3.50〔2.98–5.00〕 | 3.00–9.10 | 下调 | 4 |
| 524 | HERBERT JONES / 2024 / NBA Hoops / Haunted Hoops #182 /99 | 3.0〔1.95–4.05〕 | 3.00〔1.95–4.05〕 | 3.00–3.00 | 维持 | 1 |
| 527 | Doctor Strange / 2024 / Topps Chrome | 1.0〔0.55–159.65〕 | 证据不足 | 不足 | 转证据不足 | 0 |

*价域仅展示已核对历史记录的观测范围；不代表完整近期合理价区间。

## 实际采用的代表性来源

- #6 Stephen Curry / 2025 / Topps Signature Class Basketball / Star Cast #SC-1：[卡淘商品页](https://cardhobby.com.cn/Market/Item/125023844)。
- #21 LeBron James / 2025 / Topps NBA Hoops / Bounce House / Silver #BH-2：[卡淘商品页](https://www.cardhobby.com.cn/market/item/121800919)。
- #24 Victor Wembanyama / 2024 / Panini Donruss Basketball / Bomb Squad #6：[卡淘商品页](https://cardhobby.com.cn/Market/Item/129478877)。
- #32 VJ Edgecombe / 2025 / Court Kings / Rookies III #167：[卡淘商品页](https://cardhobby.com.cn/Market/Item/126734807)。
- #60 LeBron James / 2020 / Mosaic Basketball / Stained Glass #1：[卡淘商品页](https://cardhobby.com.cn/Market/Item/126449431)。
- #63 Shai Gilgeous-Alexander / 2025 / NBA Hoops Basketball / Oasis #O-1：[卡淘商品页](https://cardhobby.com.cn/Market/Item/113739986)。
- #69 Victor Wembanyama / 2023 / Topps Now / Rookie of the Year #VW-6：[卡淘商品页](https://cardhobby.com.cn/Market/Item/126895542)。
- #77 Michael Jordan / 1998 / Black Diamond #13：[卡淘商品页](https://cardhobby.com.cn/Market/Item/122016700)。
