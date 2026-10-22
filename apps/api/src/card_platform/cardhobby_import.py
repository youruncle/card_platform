from card_platform.model_requests import nonthinking_extra_body
import asyncio
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from openai import AsyncOpenAI
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.image_service import ImageStorageService, InvalidImageError
from card_platform.repository import ConflictError, NotFoundError, PortfolioRepository
from card_platform.schemas import (
    CardHobbyImportConfirmation,
    CardHobbyImportDraft,
    CardHobbyImportPreviewRequest,
    CardHobbyImportPreviewResult,
    CardHobbyOrderCapture,
    CardIdentityInput,
    CollectionItemCreate,
    PurchaseInput,
)


CARDHOBBY_ITEM_URL = re.compile(
    r"^https://(?:www\.)?cardhobby\.com\.cn/market/item/(?P<item_id>\d+)/?$", re.I
)
SEASON_PATTERN = re.compile(r"\b(19|20)(\d{2})(?:\s*[-/]\s*(\d{2,4}))?\b")
SERIAL_PATTERN = re.compile(r"(?:(\d+)\s*/\s*(\d+)|/\s*(\d+)|(\d+)\s*编)")
UNIT_PRICE_PATTERN = re.compile(
    r"(?:¥|￥)\s*(\d+(?:\.\d{1,2})?)\s*[xX×]\s*\d+"
)
TOTAL_PRICE_PATTERN = re.compile(
    r"(?:总额|合计)\s*[：:]?\s*(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)"
)
KNOWN_PRODUCTS = (
    "Court Kings",
    "Prizm",
    "Select",
    "Optic",
    "National Treasures",
    "Immaculate",
    "Flawless",
    "Donruss",
    "Mosaic",
)


def full_size_cardhobby_image_url(image_url: str) -> str:
    parsed = urlsplit(image_url)
    if parsed.scheme == "https" and parsed.hostname == "images.cardhobby.com":
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return image_url


def recover_sale_price(capture: CardHobbyOrderCapture) -> Decimal | None:
    if capture.sale_price is not None:
        return capture.sale_price
    text = capture.raw_text or ""
    unit_match = UNIT_PRICE_PATTERN.search(text)
    if unit_match:
        return Decimal(unit_match.group(1))
    total_match = TOTAL_PRICE_PATTERN.search(text)
    if total_match:
        return max(
            Decimal("0"),
            Decimal(total_match.group(1)) - capture.shipping_price,
        )
    return None


class CardHobbyOrderParser:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncOpenAI(
                api_key=settings.resolved_search_api_key,
                base_url=settings.resolved_search_base_url,
                timeout=settings.card_search_timeout_seconds,
                max_retries=1,
            )
            if settings.resolved_search_api_key
            else None
        )

    async def parse(
        self, owner_id: UUID, capture: CardHobbyOrderCapture
    ) -> tuple[CollectionItemCreate | None, list[str]]:
        proposed = await self._parse_with_model(owner_id, capture) if self.client else None
        if proposed is not None:
            return proposed, []
        return self._deterministic_parse(owner_id, capture)

    async def _parse_with_model(
        self, owner_id: UUID, capture: CardHobbyOrderCapture
    ) -> CollectionItemCreate | None:
        schema = json.dumps(CardIdentityInput.model_json_schema(), ensure_ascii=False)
        response = await self.client.chat.completions.create(
            model=self.settings.card_search_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "从卡淘已买入商品标题解析球星卡身份。不得把订单号、货位号、限编总数"
                        "误当 card_number。无法确定的可选字段填 null；player_name、brand_name、"
                        "season_start、product_name 必须有明确标题依据，否则返回 {}。"
                        f"只输出符合该 schema 的 JSON：{schema}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"title": capture.title, "raw_text": capture.raw_text},
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            extra_body=nonthinking_extra_body(self.settings.card_search_model),
        )
        content = response.choices[0].message.content
        if not content or content.strip() == "{}":
            return None
        try:
            card = CardIdentityInput.model_validate_json(content)
        except ValueError:
            return None
        return self._build_item(owner_id, capture, card)

    def _deterministic_parse(
        self, owner_id: UUID, capture: CardHobbyOrderCapture
    ) -> tuple[CollectionItemCreate | None, list[str]]:
        title = capture.title
        warnings: list[str] = []
        season_match = SEASON_PATTERN.search(title)
        product = next(
            (name for name in KNOWN_PRODUCTS if name.casefold() in title.casefold()), None
        )
        brand = next(
            (name for name in ("Panini", "Topps", "Upper Deck", "Leaf") if name.casefold() in title.casefold()),
            None,
        )
        if not season_match:
            warnings.append("未能从标题确定赛季")
        if not product:
            warnings.append("未能从标题确定产品")
        warnings.append("球员姓名需要确认")
        if not season_match or not product or not brand:
            return None, warnings
        return None, warnings

    @staticmethod
    def _build_item(
        owner_id: UUID,
        capture: CardHobbyOrderCapture,
        card: CardIdentityInput,
    ) -> CollectionItemCreate:
        serial_number = None
        serial_total = card.print_run
        match = SERIAL_PATTERN.search(capture.title)
        if match:
            serial_number = int(match.group(1)) if match.group(1) else None
            serial_total = int(next(value for value in match.groups()[1:] if value))
        return CollectionItemCreate(
            owner_id=owner_id,
            card=card,
            serial_number=serial_number,
            serial_total=serial_total,
            notes=f"卡淘商品：{capture.item_url}",
            purchase=PurchaseInput(
                transacted_at=capture.purchased_at or datetime.now(timezone.utc),
                price=capture.sale_price or Decimal("0"),
                fee_amount=capture.shipping_price,
                currency=capture.currency,
                venue="CardHobby",
                external_order_id=capture.external_order_id,
                notes=f"{capture.title}\n{capture.item_url}",
            ),
        )


class CardHobbyImportService:
    def __init__(self, settings: Settings, repository: PortfolioRepository) -> None:
        self.parser = CardHobbyOrderParser(settings)
        self.repository = repository
        self.image_storage = ImageStorageService(settings)
        self._parse_limit = asyncio.Semaphore(4)

    async def _parse(
        self, owner_id: UUID, capture: CardHobbyOrderCapture
    ) -> tuple[CollectionItemCreate | None, list[str]]:
        async with self._parse_limit:
            return await self.parser.parse(owner_id, capture)

    async def preview(
        self, connection: AsyncConnection, payload: CardHobbyImportPreviewRequest
    ) -> CardHobbyImportPreviewResult:
        owner = await connection.execute("SELECT id FROM app_user WHERE id = %s", (payload.owner_id,))
        if await owner.fetchone() is None:
            raise NotFoundError("owner not found")
        batch_row = await connection.execute(
            """
            INSERT INTO cardhobby_import_batch (owner_id, source_url)
            VALUES (%s, %s) RETURNING id
            """,
            (payload.owner_id, payload.source_url),
        )
        batch_id = (await batch_row.fetchone())["id"]
        drafts: list[CardHobbyImportDraft] = []
        captures = [
            capture.model_copy(update={"sale_price": recover_sale_price(capture)})
            for capture in payload.orders
        ]
        parsed_orders = await asyncio.gather(
            *(self._parse(payload.owner_id, capture) for capture in captures)
        )
        for capture, (proposed, warnings) in zip(
            captures, parsed_orders, strict=True
        ):
            match = CARDHOBBY_ITEM_URL.match(capture.item_url)
            if not match or match.group("item_id") != capture.cardhobby_item_id:
                raise ConflictError("cardhobby item URL and item id do not match")
            existing = await connection.execute(
                """
                SELECT id, status, image_urls FROM cardhobby_import_item
                WHERE owner_id = %s AND cardhobby_item_id = %s
                """,
                (payload.owner_id, capture.cardhobby_item_id),
            )
            duplicate = await existing.fetchone()
            if duplicate:
                drafts.append(
                    CardHobbyImportDraft(
                        id=duplicate["id"],
                        duplicate=True,
                        status=duplicate["status"],
                        capture=capture,
                        proposed_item=proposed,
                        warnings=["该卡淘商品已导入或已在其他批次中", *warnings],
                    )
                )
                continue
            row = await connection.execute(
                """
                INSERT INTO cardhobby_import_item (
                    batch_id, owner_id, external_order_id, cardhobby_item_id,
                    item_url, title, image_urls, purchased_at, sale_price,
                    shipping_price, currency, raw_payload, proposed_item,
                    recognition_warnings
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    batch_id,
                    payload.owner_id,
                    capture.external_order_id,
                    capture.cardhobby_item_id,
                    capture.item_url,
                    capture.title,
                    Jsonb(capture.image_urls),
                    capture.purchased_at,
                    capture.sale_price,
                    capture.shipping_price,
                    capture.currency.upper(),
                    Jsonb(capture.model_dump(mode="json")),
                    Jsonb(proposed.model_dump(mode="json")) if proposed else None,
                    Jsonb(warnings),
                ),
            )
            draft_id = (await row.fetchone())["id"]
            drafts.append(
                CardHobbyImportDraft(
                    id=draft_id,
                    status="preview",
                    capture=capture,
                    proposed_item=proposed,
                    warnings=warnings,
                )
            )
        return CardHobbyImportPreviewResult(batch_id=batch_id, drafts=drafts)

    async def confirm(
        self,
        connection: AsyncConnection,
        batch_id: UUID,
        owner_id: UUID,
        items: list[CardHobbyImportConfirmation],
    ) -> tuple[list[UUID], list[UUID]]:
        imported: list[UUID] = []
        skipped: list[UUID] = []
        for confirmation in items:
            if confirmation.item.owner_id != owner_id:
                raise ConflictError("import item owner does not match request owner")
            result = await connection.execute(
                """
                SELECT id, status, image_urls FROM cardhobby_import_item
                WHERE id = %s AND batch_id = %s AND owner_id = %s
                FOR UPDATE
                """,
                (confirmation.draft_id, batch_id, owner_id),
            )
            draft = await result.fetchone()
            if draft is None:
                raise NotFoundError("cardhobby import draft not found")
            if draft["status"] == "imported":
                skipped.append(confirmation.draft_id)
                continue
            item_id = await self.repository.create_item(connection, confirmation.item)
            for index, image_url in enumerate((draft["image_urls"] or [])[:2]):
                try:
                    await self.image_storage.save_remote_item_image(
                        connection,
                        item_id,
                        owner_id,
                        "front" if index == 0 else "back",
                        full_size_cardhobby_image_url(image_url),
                    )
                except InvalidImageError:
                    continue
            await connection.execute(
                """
                UPDATE cardhobby_import_item
                SET status = 'imported', collection_item_id = %s,
                    proposed_item = %s
                WHERE id = %s
                """,
                (
                    item_id,
                    Jsonb(confirmation.item.model_dump(mode="json")),
                    confirmation.draft_id,
                ),
            )
            imported.append(item_id)
        await connection.execute(
            """
            UPDATE cardhobby_import_batch
            SET status = 'confirmed', confirmed_at = now()
            WHERE id = %s AND owner_id = %s
            """,
            (batch_id, owner_id),
        )
        return imported, skipped
