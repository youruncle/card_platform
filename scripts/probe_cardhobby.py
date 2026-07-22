"""Run a read-only CardHobby provider probe for one representative card."""

import asyncio
import json

from openai import AsyncOpenAI

from card_platform.config import Settings
from card_platform.ingestion.cardhobby import CardHobbyProvider


async def main() -> None:
    settings = Settings()
    if not settings.vision_api_key:
        raise SystemExit("VISION_API_KEY is not configured")
    client = AsyncOpenAI(
        api_key=settings.vision_api_key,
        base_url=settings.vision_base_url,
        timeout=settings.valuation_search_timeout_seconds,
        max_retries=1,
    )
    provider = CardHobbyProvider(client, settings.valuation_search_model)
    result = await provider.search(
        {
            "player_name": "Tim Duncan",
            "brand_name": "Panini",
            "season_start": 2021,
            "season_end": 2022,
            "product_name": "Immaculate",
            "set_name": None,
            "card_number": None,
            "insert_name": None,
            "parallel_name": "闪版",
            "is_autograph": False,
            "is_patch": True,
            "print_run": 75,
            "serial_total": 75,
            "grade_company": None,
            "grade_value": None,
            "base_currency": "CNY",
            "purchase_context": (
                "2021-22 Panini Immaculate Tim Duncan 蒂姆邓肯 石佛 马刺 "
                "爱咪咪 75编 双窗 球衣 物料 75周年 75大 闪版 特卡 #2745"
            ),
        }
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
