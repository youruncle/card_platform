"""Import immutable analyses produced by an external Codex multimodal task.

This script never invokes a local valuation pipeline. It verifies that the image
URLs still resolve to the reviewed bytes and writes only source=codex_multimodal.
"""

import asyncio
import hashlib
import json
from pathlib import Path

from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.db import Database

PROMPT = "根据这张卡片的正反面图片，看看这张卡咋样？请识别卡片并分析它的价值。"
MODEL = "GPT-5 Codex (multimodal task)"
CONTRACT_VERSION = 1

# Values are broad CNY visual-analysis ranges, not local valuation output.
CARDS = {
    0: (
        "2020-21 Panini Mosaic Jaren Jackson Jr. 彩色限量 73/88",
        20,
        40,
        70,
        "编号平行卡；非新秀，球星需求中等",
    ),
    1: (
        "2025-26 Topps NBA Hoops Bounce House LeBron James BH-2",
        25,
        45,
        80,
        "LeBron 插画特卡，非编号",
    ),
    4: (
        "2025 Topps Chrome Ace Bailey RC 橙色折射",
        50,
        120,
        260,
        "新秀标识与彩色折射；背图严重缺失",
    ),
    5: (
        "2023-24 Donruss Elite Andrew Nembhard 限量折射",
        30,
        70,
        150,
        "Elite 彩色折射，编号在正面但反光难读",
    ),
    6: (
        "Topps Signature Class Stephen Curry Stars 插卡",
        25,
        55,
        100,
        "巨星插卡，未见编号或签字",
    ),
    9: (
        "2025 Topps Flagship Scottie Barnes 金色 21/50",
        120,
        220,
        380,
        "明确 /50 金色编号平行",
    ),
    10: (
        "2020-21 Mosaic Jaren Jackson Jr. 紫色 22/50",
        60,
        120,
        220,
        "明确 /50 低编紫色平行",
    ),
    12: (
        "2023-24 Prizm Brandon Miller RC 基础卡",
        15,
        30,
        60,
        "新秀基础 Prizm，未见稀缺编号",
    ),
    14: (
        "2025 Topps Flagship Scottie Barnes 金色平行",
        60,
        130,
        260,
        "金色平行；照片裁切导致编号不可确认",
    ),
    15: (
        "2025-26 Topps Chrome Luka Doncic 彩色平行",
        30,
        70,
        150,
        "球星 Chrome 平行，未见编号",
    ),
    16: ("2025-26 Topps Chrome VJ Edgecombe RC 折射", 25, 60, 140, "新秀 Chrome 折射"),
    17: (
        "2025-26 Panini Signature Series Kevin Garnett 7/25",
        180,
        350,
        650,
        "名宿低编 /25；系列名含 Signature 但图上未见亲签",
    ),
    18: (
        "2024-25 Immaculate Matas Buzelis / Tristan da Silva 双新秀球衣 2/25",
        500,
        900,
        1600,
        "高端双 RC、双球衣切片、低编 /25",
    ),
    19: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡",
        15,
        35,
        80,
        "热门新秀前景题材，非签字非编号",
    ),
    20: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡 #7",
        15,
        35,
        80,
        "热门新秀前景题材，基础版本",
    ),
    21: (
        "2025-26 Topps NBA Hoops Bounce House LeBron James",
        25,
        45,
        80,
        "巨星插画特卡",
    ),
    22: (
        "2024-25 Panini Prizm Victor Wembanyama 绿色平行 #10",
        60,
        140,
        280,
        "高需求球星彩色 Prizm 平行",
    ),
    23: (
        "2024-25 Mosaic Victor Wembanyama Jam Masters",
        20,
        45,
        90,
        "高需求球星插卡，非编号",
    ),
    24: (
        "2024-25 Donruss Victor Wembanyama Bomb Squad",
        15,
        35,
        70,
        "球星插卡，非编号",
    ),
    25: (
        "2024-25 Donruss Victor Wembanyama Magicians",
        15,
        35,
        70,
        "球星插卡，背面拍摄不完整",
    ),
    26: (
        "2023-24 Immaculate Scoot Henderson 3/10",
        500,
        900,
        1600,
        "高端 Immaculate、极低编 /10",
    ),
    27: (
        "2021 Chronicles Draft Picks Justin Champagnie Tribute Auto 24/199",
        80,
        160,
        300,
        "新秀时期亲签、编号 /199",
    ),
    28: (
        "2016-17 Panini Zach LaVine Patch Auto 35/49",
        500,
        900,
        1500,
        "比赛使用物料与亲签、低编 /49",
    ),
    29: (
        "2023-24 National Treasures Jalen Duren Penmanship Auto 25/99",
        350,
        650,
        1100,
        "高端 NT 亲签、编号 /99",
    ),
    30: (
        "2024-25 Panini One Brandon Ingram Jumbo Jersey Auto 98/125",
        280,
        550,
        950,
        "大窗球衣加签字，编号 /125；图上球队文字存在错配不确定",
    ),
    31: (
        "2025 Topps Chrome McDonald's Nate Ament Winning Tags Auto",
        180,
        400,
        800,
        "Topps 认证亲签，新秀前景波动大",
    ),
    32: (
        "2025-26 Panini Court Kings VJ Edgecombe Rookies III",
        30,
        80,
        180,
        "高阶新秀插卡 Rookies III",
    ),
    33: (
        "2024-25 Mosaic Victor Wembanyama Stare Masters",
        25,
        60,
        130,
        "高需求球星 Mosaic 插卡",
    ),
    34: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 折射",
        25,
        60,
        140,
        "热门前景球员折射平行",
    ),
    35: (
        "2025 Topps Chrome Dylan Harper Loading RC 185/199",
        80,
        180,
        380,
        "RC 插卡且编号 /199",
    ),
    36: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 基础卡",
        15,
        35,
        80,
        "热门新秀前景题材，基础版本",
    ),
    37: (
        "2025 Topps Chrome McDonald's AJ Dybantsa 108/199",
        60,
        140,
        300,
        "编号 /199 折射平行",
    ),
    38: (
        "2025-26 Topps NBA Hoops Paolo Banchero Oasis O-17",
        15,
        35,
        75,
        "球星插卡，非编号",
    ),
    39: ("2023-24 Panini One Luka Doncic 19/49", 180, 380, 750, "巨星低编 /49 平行"),
    40: (
        "2023-24 Immaculate Scoot Henderson 3/10",
        500,
        900,
        1600,
        "高端 Immaculate、极低编 /10",
    ),
    41: (
        "2022-23 Crown Royale Shaedon Sharpe Silhouettes RPA 100/199",
        700,
        1300,
        2200,
        "新秀球衣签字卡、编号 /199",
    ),
}


async def main() -> None:
    raise SystemExit('旧硬编码经验价导入已停用。请执行 scripts/codex_sales_research.py 的逐卡两市场研究流程；历史快照保留。')
    root = Path(__file__).resolve().parents[1] / "apps/api"
    db = Database(get_settings())
    await db.open()
    try:
        async with db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id,owner_id,display_id,image_front_url,image_back_url FROM collection_item WHERE deleted_at IS NULL ORDER BY display_id"
                )
            ).fetchall()
            imported = skipped = 0
            for row in rows:
                if row["display_id"] not in CARDS:
                    continue
                identity, low, fair, high, reason = CARDS[row["display_id"]]
                paths = [
                    root / row["image_front_url"].lstrip("/"),
                    root / row["image_back_url"].lstrip("/"),
                ]
                hashes = [
                    hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
                ]
                existing = await conn.execute(
                    """SELECT 1 FROM card_analysis_snapshot
                       WHERE collection_item_id=%s AND source='codex_multimodal'
                         AND raw_result->>'front_sha256'=%s
                         AND raw_result->>'back_sha256'=%s LIMIT 1""",
                    (row["id"], hashes[0], hashes[1]),
                )
                existing_row = await existing.fetchone()
                if existing_row:
                    complete = await conn.execute(
                        """SELECT 1 FROM card_analysis_snapshot
                           WHERE collection_item_id=%s AND source='codex_multimodal'
                             AND raw_result->>'front_sha256'=%s
                             AND raw_result->>'back_sha256'=%s
                             AND raw_result->>'contract_version'=%s LIMIT 1""",
                        (row["id"], hashes[0], hashes[1], str(CONTRACT_VERSION)),
                    )
                else:
                    complete = None
                if complete is not None and await complete.fetchone():
                    skipped += 1
                    continue
                raw = {
                    "contract_version": CONTRACT_VERSION,
                    "display_id": row["display_id"],
                    "collection_item_id": str(row["id"]),
                    "prompt": PROMPT,
                    "front_image_url": row["image_front_url"],
                    "back_image_url": row["image_back_url"],
                    "front_sha256": hashes[0],
                    "back_sha256": hashes[1],
                    "visual_identity": identity,
                    "card_type_and_scarcity": reason,
                    "condition_visible_risks": [
                        "照片隔着卡砖/卡套，反光与表面划痕可能来自保护层，无法替代裸卡评级"
                    ],
                    "value_reasons": [
                        reason,
                        "价值区间为 Codex 看图后的宽区间判断，未读取本地成交或估值",
                    ],
                    "uncertainties": ["未联网核验近期成交；币值区间置信度较低"],
                    "recommendation": "先核对编号与品相，再用公开成交验证；高端卡建议专业评级。",
                    "public_research_links": [],
                    "raw_text": f"{identity}。{reason}。视觉估值约 {low}-{high} CNY，公允参考 {fair} CNY。",
                }
                async with conn.transaction():
                    job = (
                        await (
                            await conn.execute(
                                "INSERT INTO card_analysis_job(owner_id,collection_item_id,status,requested_source,attempt_count,started_at,finished_at) VALUES(%s,%s,'succeeded','codex_multimodal',1,now(),now()) RETURNING id",
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
                        """INSERT INTO card_analysis_snapshot(job_id,collection_item_id,analysis_version,source,model_name,identity_summary,identity_confidence,currency,fair_value_low,fair_value,fair_value_high,valuation_confidence,evidence_summary,value_drivers,risks_and_conflicts,investment_conclusion,recommended_actions,raw_result,status) VALUES(%s,%s,%s,'codex_multimodal',%s,%s,.88,'CNY',%s,%s,%s,.35,'[]',%s,%s,%s,%s,%s,'succeeded')""",
                        (
                            job,
                            row["id"],
                            version,
                            MODEL,
                            identity,
                            low,
                            fair,
                            high,
                            Jsonb([reason]),
                            Jsonb(
                                raw["condition_visible_risks"] + raw["uncertainties"]
                            ),
                            raw["raw_text"],
                            Jsonb([raw["recommendation"]]),
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
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
