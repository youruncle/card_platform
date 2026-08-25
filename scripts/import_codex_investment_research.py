"""Append Codex image-led, public-market-researched investment snapshots."""

import asyncio
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.db import Database

PROMPT = "根据这张卡片的正反面图片，识别卡片；联网研究公开成交与系列定位，并分析收藏和投资价值、估值、合理买入价、是否值得持有及建议动作。"
MODEL = "GPT-5 Codex (multimodal + public market research)"
FX = 6.75
RESEARCH_VERSION = 1

# display_id: identity, low, fair, high, buy_max, confidence, verdict, series, source URLs
CARDS = {
    0: (
        "2020-21 Panini Mosaic Jaren Jackson Jr. 限量 73/88",
        25,
        45,
        80,
        35,
        0.45,
        "收藏持有，投资弹性有限",
        "Mosaic 中端彩色平行；/88 有稀缺性，但非新秀且球员市场深度一般",
        [
            "https://www.beckett.com/basketball/2020-21/panini-mosaic-mosaic-fast-break-purple/29-jaren-jackson-jr-20769042"
        ],
    ),
    1: (
        "2025-26 Topps NBA Hoops Bounce House LeBron James BH-2",
        20,
        40,
        70,
        30,
        0.50,
        "适合低价收藏，不建议追高",
        "Bounce House 是 Topps NBA Hoops 插画特卡；LeBron 提供稳定收藏需求，但无编号",
        ["https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/"],
    ),
    4: (
        "2025-26 Topps Chrome Ace Bailey #255 Orange Refractor RC /25（需复核）",
        600,
        1700,
        3000,
        1200,
        0.58,
        "高波动新秀标的，低价可持有",
        "Topps Chrome 核心新秀低编平行；真 Orange /25 稀缺，但照片背面不完整，必须排除非编号 Orange Basketball Border",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2025-topps-chrome/ace-bailey-orange-refractor-255"
        ],
    ),
    5: (
        "2023-24 Donruss Elite Andrew Nembhard #83 彩色平行（编号需复核）",
        20,
        45,
        100,
        35,
        0.42,
        "以球队/球员收藏为主",
        "Elite 属中端产品，平行价值高度取决于具体颜色和编号；照片反光使版本无法完全锁定",
        [
            "https://www.beckett.com/news/2023-24-donruss-elite-basketball-cards/",
            "https://www.ebay.com/b/2023-24-Donruss-Elite-Andrew-Nembhard-83/212/bn_7123396204",
        ],
    ),
    6: (
        "2025-26 Topps Signature Class Stephen Curry Star Cast SSP SC-1",
        500,
        675,
        850,
        540,
        0.75,
        "值得持有；成交活跃时可兑现",
        "Star Cast 为 Signature Class SSP 插卡，Curry 的巨星收藏面显著强于普通插卡",
        ["https://www.ebay.com/itm/336620362361"],
    ),
    9: (
        "2025 Topps Flagship Scottie Barnes Gold 21/50",
        90,
        170,
        300,
        125,
        0.52,
        "可持有但不宜重仓",
        "Flagship 金色 /50 具明确稀缺性；价值仍主要受 Barnes 表现与该新产品长期认可度驱动",
        ["https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/"],
    ),
    10: (
        "2020-21 Mosaic Jaren Jackson Jr. Purple 22/50",
        60,
        110,
        190,
        80,
        0.55,
        "收藏持有，逢高可减",
        "Mosaic Purple /50 低编明确，但非新秀年限制长期升值空间",
        [
            "https://www.beckett.com/basketball/2020-21/panini-mosaic-mosaic-fast-break-purple/29-jaren-jackson-jr-20769042"
        ],
    ),
    12: (
        "2023-24 Panini Prizm Brandon Miller RC",
        10,
        25,
        55,
        18,
        0.48,
        "低成本新秀收藏；不建议评级普通基础版",
        "Prizm 是主流新秀品牌，但基础/普通插卡发行量大，只有高分或稀缺平行才有明显投资性",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2023-panini-prizm-emergent/brandon-miller-17"
        ],
    ),
    14: (
        "2025 Topps Flagship Scottie Barnes Gold Parallel（编号不可见）",
        50,
        100,
        220,
        70,
        0.38,
        "先复核编号再决定持有",
        "金色外观不等于 /50；裁切照片使稀缺度无法确认，因此不能按低编成交定价",
        ["https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/"],
    ),
    15: (
        "2025-26 Topps Chrome Luka Doncic 彩色平行（版本待复核）",
        35,
        80,
        180,
        55,
        0.42,
        "巨星收藏可持有，先确认平行名称",
        "Topps Chrome 流动性较好，Luka 有长期收藏基本盘；具体平行和是否编号决定大部分溢价",
        ["https://cardgrader.ai/sports-card-database/basketball/2025-topps-chrome"],
    ),
    16: (
        "2025-26 Topps Chrome VJ Edgecombe #253 Refractor RC",
        180,
        300,
        500,
        230,
        0.62,
        "可持有但新秀价格波动极大",
        "Topps Chrome 核心新秀 Refractor；高分卡溢价很大，裸卡不应直接套用 PSA 10 成交",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2025-topps-chrome/vj-edgecombe-refractor-253",
            "https://www.psacard.com/cert/147318766/psa",
        ],
    ),
    17: (
        "2025-26 Panini Signature Series Kevin Garnett 7/25（图上未见亲签）",
        120,
        240,
        450,
        180,
        0.44,
        "名宿低编收藏；确认是否真签后再估",
        "低编 /25 和 Garnett 提供收藏性，但系列名含 Signature 不代表该卡一定有签名",
        ["https://www.ebay.com/sch/i.html?_nkw=Kevin+Garnett+Signature+Series+7%2F25"],
    ),
    18: (
        "2024-25 Immaculate Matas Buzelis / Tristan da Silva 双新秀球衣 2/25",
        250,
        450,
        800,
        340,
        0.52,
        "适合新秀组合收藏，投资需控制仓位",
        "Immaculate 高端品牌、双 RC、双物料与 /25 是优点；双人卡需求通常弱于单人核心 RPA",
        ["https://www.beckett.com/news/2023-24-panini-immaculate-basketball-cards/"],
    ),
    19: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡",
        15,
        30,
        55,
        22,
        0.55,
        "低价收藏，短期不值得评级",
        "热门选秀前景题材，但基础版供给大且会被后续 NBA 正式新秀卡稀释",
        [
            "https://theclector.com/players/aj-dybantsa-56948",
            "https://www.steelcitycollectibles.com/storage/pdf/product_checklists/2025-Topps-Chrome-McDonald-s-All-American-Basketball-Checklist.pdf",
        ],
    ),
    20: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡 #7",
        15,
        30,
        55,
        22,
        0.55,
        "低价收藏，短期不值得评级",
        "前景球员早期卡有收藏意义，但基础版本不稀缺，投资回报依赖球员兑现天赋",
        ["https://theclector.com/players/aj-dybantsa-56948"],
    ),
    21: (
        "2025-26 Topps NBA Hoops Bounce House LeBron James",
        20,
        40,
        70,
        30,
        0.50,
        "适合长期球星收藏",
        "插画设计和 LeBron 题材有收藏性；非编号普通插卡投资上限有限",
        ["https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/"],
    ),
    22: (
        "2024-25 Panini Prizm Fractal Green Victor Wembanyama #10",
        20,
        38,
        65,
        28,
        0.75,
        "可收藏持有，但不应按新秀卡溢价",
        "Wembanyama 二年卡需求较强；Fractal Green 近期裸卡成交多在约 US$2-8",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2024-panini-prizm-fractal/victor-wembanyama-10"
        ],
    ),
    23: (
        "2024-25 Mosaic Victor Wembanyama Jam Masters #4",
        15,
        30,
        55,
        22,
        0.55,
        "球星插卡收藏，投资性较弱",
        "Mosaic Jam Masters 有辨识度但基础版不稀缺，低编/彩色版本才有更强投资价值",
        ["https://www.beckett.com/news/2024-25-panini-mosaic-basketball-cards/"],
    ),
    24: (
        "2024-25 Donruss Victor Wembanyama Bomb Squad #6",
        35,
        50,
        75,
        38,
        0.75,
        "低价可持有；高于区间不追",
        "Donruss 大众插卡，Wembanyama 提供需求；公开裸卡成交约 US$7.20",
        [
            "https://www.fanaticscollect.com/buy-now/07922516-9530-4bdc-aa26-5da2d7f23bbf/2024-25-donruss-bomb-squad-6-victor-wembanyama"
        ],
    ),
    25: (
        "2024-25 Donruss Victor Wembanyama Magicians #9",
        15,
        32,
        60,
        24,
        0.50,
        "适合球星收藏，普通版不建议评级",
        "Magicians 是大众插卡；照片背面不完整，若是编号 Laser 平行则需重新估值",
        [
            "https://www.comc.com/Cards/Basketball/2024-25/Panini_Donruss_-_Magicians/9/Victor_Wembanyama/28159005"
        ],
    ),
    26: (
        "2023-24 Immaculate Scoot Henderson 3/10",
        250,
        500,
        900,
        360,
        0.45,
        "高风险低编新秀卡；仅小仓位持有",
        "Immaculate 和 /10 稀缺性强，但具体子集/是否签字未完全确认，Scoot 市场表现风险高",
        ["https://www.beckett.com/news/2023-24-panini-immaculate-basketball-cards/"],
    ),
    27: (
        "2021 Chronicles Draft Picks Justin Champagnie Tribute Auto 24/199",
        35,
        70,
        130,
        50,
        0.48,
        "球员收藏为主，不建议投资重仓",
        "大学/选秀产品、/199 亲签；球员市场需求较窄，流动性有限",
        [
            "https://www.ebay.com/sch/i.html?_nkw=Justin+Champagnie+Tribute+Auto+%2F199&LH_Sold=1"
        ],
    ),
    28: (
        "Zach LaVine Patch Auto 35/49（具体年份/子集需复核）",
        250,
        420,
        750,
        320,
        0.55,
        "可收藏持有，价格合适再买",
        "成熟球星低编物料签字卡有稳定收藏性；同类 /49 成交差异大，具体品牌和物料声明关键",
        [
            "https://www.fanaticscollect.com/weekly/9dda4efa-3af3-11f0-be3e-0a58a9feac02/2023-national-treasures-clutch-factor-zach-lavine-patch-auto-49-cgc-auth",
            "https://www.sportscardinvestor.com/cards/zach-lavine-basketball/2019-one-and-one-timeless-moments-autographs-blue-tm-zlv",
        ],
    ),
    29: (
        "2023-24 National Treasures Jalen Duren Penmanship Auto（照片编号与清单待复核）",
        170,
        250,
        380,
        190,
        0.58,
        "可持有，不建议因 NT 品牌盲目追价",
        "National Treasures 高端品牌提供收藏性，但 Penmanship 是非新秀签字；公开 /49 成交约 US$29-40",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2023-panini-national-treasures-penmanship/jalen-duren-29",
            "https://www.beckett.com/news/2023-24-panini-national-treasures-basketball-cards/",
        ],
    ),
    30: (
        "2024-25 Panini One Brandon Ingram Jumbo Jersey Auto 98/125（版本待复核）",
        110,
        180,
        300,
        135,
        0.48,
        "低价可收藏，投资吸引力一般",
        "大窗物料加签名视觉效果好，但 Ingram 市场和版本流动性有限；公开相近 /75 成交约 US$20-25",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2024-panini-one-and-one-jumbo-jersey-autograph/brandon-ingram-3"
        ],
    ),
    31: (
        "2025 Topps Chrome McDonald's Nate Ament Winning Tags Auto",
        400,
        800,
        1500,
        600,
        0.42,
        "高波动前景签字卡；只适合风险仓位",
        "Winning Tags 是抢眼的前景球员签字系列，但缺少可核实同版成交，价值高度依赖选秀和后续 NBA 表现",
        [
            "https://www.ebay.com/itm/318050762602",
            "https://www.steelcitycollectibles.com/storage/pdf/product_checklists/2025-Topps-Chrome-McDonald-s-All-American-Basketball-Checklist.pdf",
        ],
    ),
    32: (
        "2025-26 Panini Court Kings VJ Edgecombe Rookies III",
        60,
        130,
        260,
        95,
        0.45,
        "设计型新秀卡可持有，别按 Chrome 核心 RC 追价",
        "Court Kings Rookies III 有层级和艺术收藏性，但市场认可度通常低于 Prizm/Topps Chrome 核心新秀",
        [
            "https://www.ebay.com/sch/i.html?_nkw=VJ+Edgecombe+Court+Kings+Rookies+III&LH_Sold=1"
        ],
    ),
    33: (
        "2024-25 Mosaic Victor Wembanyama Stare Masters",
        20,
        40,
        75,
        30,
        0.48,
        "低价球星收藏；普通版投资性有限",
        "Mosaic 设计插卡，Wembanyama 有需求但属于二年非编号卡",
        ["https://www.beckett.com/news/2024-25-panini-mosaic-basketball-cards/"],
    ),
    34: (
        "2025 Topps Chrome McDonald's AJ Dybantsa Refractor",
        45,
        80,
        140,
        60,
        0.58,
        "可小仓位持有，注意后续产品稀释",
        "Refractor 强于基础版，但仍是 NBA 前的前景卡；相近 High Rises Refractor 成交约 US$12",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2025-topps-chrome-mcdonald%27s-all-american-high-rises-one-trip-ticket/aj-dybantsa-refractor-hr-7"
        ],
    ),
    35: (
        "2025 Topps Chrome Dylan Harper Loading RC 185/199",
        450,
        800,
        1400,
        600,
        0.48,
        "高潜力高波动，可持有但不追高",
        "核心新秀、编号 /199 和热门插卡带来溢价；Dylan Harper Chrome 市场火热但不同平行/评级价差巨大",
        [
            "https://cardladder.com/players/Dylan%20Harper",
            "https://www.bigapplecollects.com/nba/cards/dylan-harper-2025-26-topps-chrome-252",
        ],
    ),
    36: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡",
        15,
        30,
        55,
        22,
        0.55,
        "低价收藏，不建议高成本评级",
        "热门前景题材但基础版供给量大，长期要面对 NBA 正式新秀卡替代",
        ["https://theclector.com/players/aj-dybantsa-56948"],
    ),
    37: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 108/199",
        70,
        140,
        260,
        105,
        0.52,
        "可小仓位持有，选秀热度高时考虑兑现",
        "编号 /199 强于基础版，但前景卡价格易被后续正式 NBA 产品稀释",
        [
            "https://www.mercari.com/us/item/m76420040545/",
            "https://theclector.com/players/aj-dybantsa-56948",
        ],
    ),
    38: (
        "2025-26 Topps NBA Hoops Paolo Banchero Oasis O-17",
        12,
        28,
        55,
        20,
        0.45,
        "球员/设计收藏为主",
        "Oasis 是非编号插卡；Paolo 有球星潜力，但普通插卡供给限制投资回报",
        ["https://www.beckett.com/news/2025-26-topps-nba-hoops-basketball-cards/"],
    ),
    39: (
        "2023-24 Panini One Luka Doncic 19/49（具体子集需复核）",
        160,
        320,
        650,
        240,
        0.40,
        "巨星低编值得持有，确认是否签字后重估",
        "Luka 与 /49 提供收藏基础，但 Panini One/One and One 子集差异巨大；不能把 Timeless Moments Auto 成交直接套用",
        [
            "https://www.beckett.com/news/2023-24-panini-one-and-one-basketball-cards/",
            "https://www.breakninja.com/basketball/23-24-Panini-One-And-One-Basketball-Checklist-Dallas-Mavericks.php",
        ],
    ),
    40: (
        "2023-24 Immaculate Scoot Henderson 3/10",
        250,
        500,
        900,
        360,
        0.45,
        "高风险低编新秀卡；仅小仓位持有",
        "Immaculate /10 稀缺，但必须确认具体子集与是否签字；球员兑现风险高",
        ["https://www.beckett.com/news/2023-24-panini-immaculate-basketball-cards/"],
    ),
    41: (
        "2022-23 Crown Royale Shaedon Sharpe Rookie Silhouettes #112 RPA 100/199",
        220,
        280,
        450,
        240,
        0.72,
        "现价附近可收藏持有，高于 450 元不追",
        "Crown Royale Rookie Silhouettes 是大窗新秀物料签字系列；近期同款裸卡成交约 US$32.88-35",
        [
            "https://www.sportscardspro.com/game/basketball-cards-2022-panini-crown-royale/shaedon-sharpe-112",
            "https://www.sportscardinvestor.com/cards/shaedon-sharpe-basketball/2022-crown-royale-rookie-silhouettes-112",
            "https://www.beckett.com/news/2022-23-panini-crown-royale-basketball-cards/",
        ],
    ),
}


async def main() -> None:
    root = Path(__file__).resolve().parents[1] / "apps/api"
    db = Database(get_settings())
    await db.open()
    imported = skipped = 0
    try:
        async with db.connection() as conn:
            rows = await (
                await conn.execute(
                    """SELECT id,owner_id,display_id,image_front_url,image_back_url FROM collection_item WHERE deleted_at IS NULL ORDER BY display_id"""
                )
            ).fetchall()
            for row in rows:
                data = CARDS.get(row["display_id"])
                if not data:
                    continue
                exists = await conn.execute(
                    """SELECT 1 FROM card_analysis_snapshot WHERE collection_item_id=%s AND source='codex_multimodal' AND raw_result->>'investment_research_version'=%s LIMIT 1""",
                    (row["id"], str(RESEARCH_VERSION)),
                )
                if await exists.fetchone():
                    skipped += 1
                    continue
                (
                    identity,
                    low,
                    fair,
                    high,
                    buy_max,
                    confidence,
                    verdict,
                    series,
                    links,
                ) = data
                paths = [
                    root / row["image_front_url"].lstrip("/"),
                    root / row["image_back_url"].lstrip("/"),
                ]
                hashes = [
                    hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
                ]
                drivers = [
                    series,
                    f"建议最高买入价约 CNY {buy_max}；公允参考 CNY {fair}",
                ]
                risks = [
                    "估值以裸卡、正常品相为前提；卡砖/卡套反光不能替代裸卡品相检查",
                    "公开成交可能存在具体平行、编号、品相、评级、运费和成交口径差异",
                    "体育卡不是稳定收益资产，球员表现、产品供给和市场热度会造成大幅波动",
                ]
                conclusion = f"{identity}。系列判断：{series}。合理估值 CNY {low}-{high}，公允参考 CNY {fair}，建议买入不高于 CNY {buy_max}。持有结论：{verdict}。"
                raw = {
                    "investment_research_version": RESEARCH_VERSION,
                    "display_id": row["display_id"],
                    "collection_item_id": str(row["id"]),
                    "prompt": PROMPT,
                    "front_image_url": row["image_front_url"],
                    "back_image_url": row["image_back_url"],
                    "front_sha256": hashes[0],
                    "back_sha256": hashes[1],
                    "visual_identity": identity,
                    "series_analysis": series,
                    "buy_price_max": buy_max,
                    "fair_value": fair,
                    "value_range": [low, high],
                    "currency": "CNY",
                    "fx_reference": FX,
                    "hold_verdict": verdict,
                    "collection_value": series,
                    "investment_value": verdict,
                    "public_research_links": links,
                    "research_method": "Only the front/back images established identity; public research terms were derived from that visual identity. Local valuations and local market comps were not used.",
                    "uncertainties": risks,
                    "raw_text": conclusion,
                }
                evidence = [
                    {
                        "type": "public_research",
                        "url": url,
                        "title": "公开清单、成交或市场参考",
                    }
                    for url in links
                ]
                async with conn.transaction():
                    job = (
                        await (
                            await conn.execute(
                                """INSERT INTO card_analysis_job(owner_id,collection_item_id,status,requested_source,attempt_count,started_at,finished_at) VALUES(%s,%s,'succeeded','codex_multimodal',1,now(),now()) RETURNING id""",
                                (row["owner_id"], row["id"]),
                            )
                        ).fetchone()
                    )["id"]
                    version = (
                        await (
                            await conn.execute(
                                "SELECT COALESCE(max(analysis_version),0)+1 version FROM card_analysis_snapshot WHERE collection_item_id=%s",
                                (row["id"],),
                            )
                        ).fetchone()
                    )["version"]
                    await conn.execute(
                        """INSERT INTO card_analysis_snapshot(job_id,collection_item_id,analysis_version,source,model_name,identity_summary,identity_confidence,currency,fair_value_low,fair_value,fair_value_high,valuation_confidence,evidence_summary,value_drivers,risks_and_conflicts,investment_conclusion,recommended_actions,raw_result,status) VALUES(%s,%s,%s,'codex_multimodal',%s,%s,.92,'CNY',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'succeeded')""",
                        (
                            job,
                            row["id"],
                            version,
                            MODEL,
                            identity,
                            low,
                            fair,
                            high,
                            confidence,
                            Jsonb(evidence),
                            Jsonb(drivers),
                            Jsonb(risks),
                            conclusion,
                            Jsonb(
                                [
                                    f"买入价不高于 CNY {buy_max}",
                                    verdict,
                                    "成交前复核具体平行、编号和裸卡品相",
                                ]
                            ),
                            Jsonb(raw),
                        ),
                    )
                imported += 1
        print(
            json.dumps(
                {
                    "imported": imported,
                    "skipped": skipped,
                    "source": "codex_multimodal",
                    "research_version": RESEARCH_VERSION,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
