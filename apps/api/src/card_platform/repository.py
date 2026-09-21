from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.redemption import redemption_identity
from card_platform.domain.portfolio import LedgerEntry, TransactionKind, calculate_position
from card_platform.schemas import (
    CardRecognitionResult,
    CollectionItemCreate,
    PortfolioItem,
    PortfolioItemQuickUpdate,
    PortfolioItemDetail,
    PortfolioSummary,
    TransactionCreate,
    TransactionType,
    TransactionView,
    UserCreate,
    ValuationCreate,
    ValuationView,
)


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def build_portfolio_summary(
    items: list[PortfolioItem], owner_id: UUID, base_currency: str
) -> PortfolioSummary:
    total_cost = sum((item.remaining_cost for item in items), Decimal("0"))
    realized = sum((item.realized_profit for item in items), Decimal("0"))
    valued_items = [
        item
        for item in items
        if item.fair_value is not None and item.valuation_currency == base_currency
    ]
    valued_ids = {item.id for item in valued_items}
    valued_market_value = sum(
        (item.fair_value * item.quantity_held for item in valued_items),
        Decimal("0"),
    )
    # A portfolio total must cover every open holding. Until a reliable market
    # valuation exists, use the remaining cost as a neutral provisional value
    # rather than silently treating the card as worth zero.
    provisional_market_value = sum(
        (item.remaining_cost for item in items if item.id not in valued_ids),
        Decimal("0"),
    )
    unrealized = sum(
        (item.unrealized_profit for item in valued_items if item.unrealized_profit is not None),
        Decimal("0"),
    )
    return PortfolioSummary(
        owner_id=owner_id,
        base_currency=base_currency,
        item_count=len(items),
        valued_item_count=len(valued_items),
        unvalued_item_count=len(items) - len(valued_items),
        total_cost=total_cost,
        total_market_value=valued_market_value + provisional_market_value,
        unrealized_profit=unrealized,
        realized_profit=realized,
    )


class NotFoundError(Exception):
    pass


class PositionError(Exception):
    pass


class ConflictError(Exception):
    pass


class PortfolioRepository:
    async def create_user(self, connection: AsyncConnection, payload: UserCreate) -> UUID:
        result = await connection.execute(
            """
            INSERT INTO app_user (email, display_name, base_currency)
            VALUES (%s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                base_currency = EXCLUDED.base_currency,
                updated_at = now()
            RETURNING id
            """,
            (payload.email.casefold(), payload.display_name, payload.base_currency.upper()),
        )
        return (await result.fetchone())["id"]

    async def create_item(self, connection: AsyncConnection, payload: CollectionItemCreate) -> UUID:
        async with connection.transaction():
            owner = await connection.execute(
                "SELECT id FROM app_user WHERE id = %s", (payload.owner_id,)
            )
            if await owner.fetchone() is None:
                raise NotFoundError("owner not found")

            player_id = await self._get_or_create_player(connection, payload)
            brand_id = await self._get_or_create_brand(connection, payload)
            card_set_id = await self._get_or_create_set(connection, brand_id, payload)
            definition_id = await self._get_or_create_definition(
                connection, player_id, card_set_id, payload
            )

            result = await connection.execute(
                """
                INSERT INTO collection_item (
                    owner_id, card_definition_id, serial_number, serial_total,
                    is_numbered,
                    grade_company, grade_value, grade_cert_number, strategy_tags,
                    acquired_at, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.owner_id,
                    definition_id,
                    payload.serial_number,
                    payload.serial_total,
                    payload.is_numbered,
                    payload.grade_company,
                    payload.grade_value,
                    payload.grade_cert_number,
                    payload.strategy_tags,
                    payload.purchase.transacted_at.date() if payload.purchase else None,
                    payload.notes,
                ),
            )
            item_id = (await result.fetchone())["id"]

            if payload.purchase:
                purchase = payload.purchase
                await connection.execute(
                    """
                    INSERT INTO card_transaction (
                        collection_item_id, transaction_type, transacted_at, quantity,
                        unit_price, fee_amount, currency, fx_rate_to_base, venue,
                        external_order_id, notes
                    ) VALUES (%s, 'buy', %s, 1, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item_id,
                        purchase.transacted_at,
                        purchase.price,
                        purchase.fee_amount,
                        purchase.currency.upper(),
                        purchase.fx_rate_to_base,
                        purchase.venue,
                        purchase.external_order_id,
                        purchase.notes,
                    ),
                )
            await connection.execute(
                """
                INSERT INTO valuation_job (collection_item_id, status)
                VALUES (%s, 'queued')
                ON CONFLICT (collection_item_id)
                    WHERE status IN ('queued', 'identifying', 'searching', 'calculating')
                DO NOTHING
                """,
                (item_id,),
            )
            return item_id

    async def _get_or_create_player(
        self, connection: AsyncConnection, payload: CollectionItemCreate
    ) -> UUID:
        card = payload.card
        result = await connection.execute(
            """
            SELECT id FROM player
            WHERE normalized_name = %s AND birth_date IS NOT DISTINCT FROM %s
            """,
            (normalize_name(card.player_name), card.player_birth_date),
        )
        row = await result.fetchone()
        if row:
            return row["id"]
        result = await connection.execute(
            """
            INSERT INTO player (full_name, normalized_name, birth_date)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (card.player_name, normalize_name(card.player_name), card.player_birth_date),
        )
        return (await result.fetchone())["id"]

    async def _get_or_create_brand(
        self, connection: AsyncConnection, payload: CollectionItemCreate
    ) -> UUID:
        card = payload.card
        result = await connection.execute(
            """
            INSERT INTO brand (name, manufacturer, tier)
            VALUES (%s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                manufacturer = COALESCE(brand.manufacturer, EXCLUDED.manufacturer),
                tier = COALESCE(brand.tier, EXCLUDED.tier)
            RETURNING id
            """,
            (card.brand_name, card.manufacturer, card.brand_tier),
        )
        return (await result.fetchone())["id"]

    async def _get_or_create_set(
        self, connection: AsyncConnection, brand_id: UUID, payload: CollectionItemCreate
    ) -> UUID:
        card = payload.card
        result = await connection.execute(
            """
            SELECT id, season_end FROM card_set
            WHERE brand_id = %s AND season_start = %s AND product_name = %s
              AND set_name IS NOT DISTINCT FROM %s
            ORDER BY (season_end IS NOT DISTINCT FROM %s) DESC, created_at DESC
            LIMIT 1
            """,
            (
                brand_id,
                card.season_start,
                card.product_name,
                card.set_name,
                card.season_end,
            ),
        )
        row = await result.fetchone()
        if row:
            if (
                card.season_end == card.season_start + 1
                and row["season_end"] in {None, card.season_start}
            ):
                # Scanner imports may initially know only the copyright/start
                # year. When later physical/checklist evidence establishes a
                # normal cross-year season, upgrade the shared product row so
                # the collection projection does not keep showing 2025/2025
                # beside a confirmed 2025/26 identity.
                await connection.execute(
                    "UPDATE card_set SET season_end = %s WHERE id = %s",
                    (card.season_end, row["id"]),
                )
            return row["id"]
        result = await connection.execute(
            """
            INSERT INTO card_set (
                brand_id, season_start, season_end, product_name, set_name
            ) VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (brand_id, card.season_start, card.season_end, card.product_name, card.set_name),
        )
        return (await result.fetchone())["id"]

    async def _get_or_create_definition(
        self,
        connection: AsyncConnection,
        player_id: UUID,
        card_set_id: UUID,
        payload: CollectionItemCreate,
    ) -> UUID:
        card = payload.card
        result = await connection.execute(
            """
            INSERT INTO card_definition (
                card_set_id, player_id, card_number, insert_name, parallel_name,
                is_rookie, is_autograph, autograph_type,
                has_inscription, inscription_text,
                is_memorabilia, memorabilia_types, memorabilia_usage,
                memorabilia_player, is_patch, is_case_hit, print_run
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (
                card_set_id, player_id,
                (COALESCE(card_number, '')),
                (COALESCE(insert_name, '')),
                (COALESCE(parallel_name, ''))
            ) DO UPDATE SET
                is_rookie = card_definition.is_rookie OR EXCLUDED.is_rookie,
                is_autograph = card_definition.is_autograph OR EXCLUDED.is_autograph,
                autograph_type = CASE
                    WHEN card_definition.autograph_type IN ('none', 'unknown')
                    THEN EXCLUDED.autograph_type
                    ELSE card_definition.autograph_type
                END,
                has_inscription = (
                    card_definition.has_inscription OR EXCLUDED.has_inscription
                ),
                inscription_text = COALESCE(
                    card_definition.inscription_text,
                    EXCLUDED.inscription_text
                ),
                is_memorabilia = (
                    card_definition.is_memorabilia OR EXCLUDED.is_memorabilia
                ),
                memorabilia_types = ARRAY(
                    SELECT DISTINCT unnest(
                        card_definition.memorabilia_types
                        || EXCLUDED.memorabilia_types
                    )
                ),
                memorabilia_usage = CASE
                    WHEN card_definition.memorabilia_usage IN ('none', 'unknown')
                    THEN EXCLUDED.memorabilia_usage
                    ELSE card_definition.memorabilia_usage
                END,
                memorabilia_player = COALESCE(
                    card_definition.memorabilia_player,
                    EXCLUDED.memorabilia_player
                ),
                is_patch = card_definition.is_patch OR EXCLUDED.is_patch,
                is_case_hit = card_definition.is_case_hit OR EXCLUDED.is_case_hit,
                print_run = COALESCE(card_definition.print_run, EXCLUDED.print_run)
            RETURNING id
            """,
            (
                card_set_id,
                player_id,
                card.card_number,
                card.insert_name,
                card.parallel_name,
                card.is_rookie,
                card.is_autograph,
                card.autograph_type,
                card.has_inscription,
                card.inscription_text,
                card.is_memorabilia,
                card.memorabilia_types,
                card.memorabilia_usage,
                card.memorabilia_player,
                card.is_patch,
                card.is_case_hit,
                card.print_run,
            ),
        )
        return (await result.fetchone())["id"]

    async def add_transaction(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        payload: TransactionCreate,
    ) -> UUID:
        async with connection.transaction():
            item = await connection.execute(
                """
                SELECT id FROM collection_item
                WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
                FOR UPDATE
                """,
                (item_id, owner_id),
            )
            if await item.fetchone() is None:
                raise NotFoundError("collection item not found")

            if payload.transaction_type == TransactionType.SELL:
                entries = await self._ledger_entries(connection, item_id)
                position = calculate_position(entries)
                if payload.quantity > position.quantity_held:
                    raise PositionError("sell quantity exceeds current position")

            result = await connection.execute(
                """
                INSERT INTO card_transaction (
                    collection_item_id, transaction_type, transacted_at, quantity,
                    unit_price, fee_amount, currency, fx_rate_to_base, venue,
                    external_order_id, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    item_id,
                    payload.transaction_type.value,
                    payload.transacted_at,
                    payload.quantity,
                    payload.unit_price,
                    payload.fee_amount,
                    payload.currency.upper(),
                    payload.fx_rate_to_base,
                    payload.venue,
                    payload.external_order_id,
                    payload.notes,
                ),
            )
            return (await result.fetchone())["id"]

    async def add_valuation(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        payload: ValuationCreate,
    ) -> UUID:
        exists = await connection.execute(
            """
            SELECT id FROM collection_item
            WHERE id = %s AND owner_id = %s AND deleted_at IS NULL
            """,
            (item_id, owner_id),
        )
        if await exists.fetchone() is None:
            raise NotFoundError("collection item not found")
        result = await connection.execute(
            """
            INSERT INTO valuation_snapshot (
                collection_item_id, valued_at, method, currency, low_value,
                fair_value, high_value, confidence, evidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                item_id,
                payload.valued_at or datetime.now(timezone.utc),
                payload.method.value,
                payload.currency.upper(),
                payload.low_value,
                payload.fair_value,
                payload.high_value,
                payload.confidence,
                Jsonb(payload.evidence),
            ),
        )
        return (await result.fetchone())["id"]

    async def list_items(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        *,
        deleted_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
        item_ids: list[UUID] | None = None,
    ) -> list[PortfolioItem]:
        deleted_filter = "ci.deleted_at IS NOT NULL" if deleted_only else "ci.deleted_at IS NULL"
        pagination = ""
        parameters: tuple[object, ...] = (owner_id,)
        item_filter = ""
        if item_ids is not None:
            item_filter = "AND ci.id = ANY(%s::uuid[])"
            parameters += (item_ids,)
        if limit is not None:
            pagination = "LIMIT %s OFFSET %s"
            parameters += (limit, offset)
        result = await connection.execute(
            f"""
            SELECT
                ci.id, ci.display_id, ci.owner_id, p.id AS player_id, p.full_name AS player_name,
                b.name AS brand_name,
                cs.season_start, cs.season_end, cs.product_name, cs.set_name,
                cd.card_number, cd.insert_name, cd.parallel_name,
                cd.is_rookie, cd.is_autograph, cd.autograph_type,
                cd.has_inscription, cd.inscription_text,
                cd.is_memorabilia, cd.memorabilia_types,
                cd.memorabilia_usage, cd.memorabilia_player, cd.is_patch,
                ci.is_numbered,
                ci.serial_number, ci.serial_total, ci.grade_company, ci.grade_value,
                ci.strategy_tags, ci.image_front_url, ci.image_back_url, u.base_currency,
                purchase.unit_price AS purchase_price,
                purchase.unit_price * purchase.quantity + purchase.fee_amount AS purchase_total_cost,
                purchase.currency AS purchase_currency,
                ci.deleted_at, ci.deleted_reason,
                ci.notes AS redemption_notes,
                scanner_recognition.redemption_recognition,
                scanner_recognition.recognition_confidence,
                scanner_recognition.recognition_status,
                COALESCE(
                    scanner_recognition.recognition_quality_issues,
                    '[]'::jsonb
                ) AS recognition_quality_issues,
                CASE WHEN vj.created_at > lv.valued_at AND vj.status <> 'succeeded'
                     THEN NULL ELSE lv.currency END AS valuation_currency,
                CASE WHEN vj.created_at > lv.valued_at AND vj.status <> 'succeeded'
                     THEN NULL ELSE lv.low_value END AS valuation_low,
                CASE WHEN vj.created_at > lv.valued_at AND vj.status <> 'succeeded'
                     THEN NULL ELSE lv.fair_value END AS fair_value,
                CASE WHEN vj.created_at > lv.valued_at AND vj.status <> 'succeeded'
                     THEN NULL ELSE lv.high_value END AS valuation_high,
                sv.currency AS shadow_valuation_currency,
                sv.low_value AS shadow_valuation_low,
                sv.fair_value AS shadow_fair_value,
                sv.high_value AS shadow_valuation_high,
                sv.confidence AS shadow_valuation_confidence,
                sv.status AS shadow_valuation_status,
                sv.model_version AS shadow_valuation_model,
                vj.id AS valuation_job_id,
                CASE WHEN lv.valued_at > vj.created_at THEN 'succeeded'
                     ELSE vj.status END AS valuation_status,
                CASE WHEN lv.valued_at > vj.created_at THEN NULL
                     ELSE vj.error_message END AS valuation_error,
                CASE WHEN lv.valued_at > vj.created_at THEN '{{}}'::jsonb
                     ELSE COALESCE(vj.result_summary, '{{}}'::jsonb)
                END AS valuation_result_summary,
                (SELECT COUNT(*) FROM market_comp mc
                 WHERE mc.card_definition_id = ci.card_definition_id) AS market_comp_count
            FROM collection_item ci
            JOIN app_user u ON u.id = ci.owner_id
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            JOIN player p ON p.id = cd.player_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            JOIN brand b ON b.id = cs.brand_id
            LEFT JOIN latest_item_valuation lv ON lv.collection_item_id = ci.id
            LEFT JOIN LATERAL (
                SELECT unit_price, currency, quantity, fee_amount
                FROM card_transaction
                WHERE collection_item_id = ci.id AND transaction_type = 'buy'
                ORDER BY transacted_at, created_at, id
                LIMIT 1
            ) purchase ON true
            LEFT JOIN LATERAL (
                SELECT currency, low_value, fair_value, high_value, confidence,
                       status, model_version
                FROM valuation_shadow_result
                WHERE collection_item_id = ci.id
                  AND valuation_job_id = (
                      SELECT id FROM valuation_job
                      WHERE collection_item_id = ci.id
                      ORDER BY created_at DESC LIMIT 1
                  )
                  AND identity_version_id = (
                      SELECT confirmed_version.id
                      FROM card_identity_version confirmed_version
                      WHERE confirmed_version.collection_item_id = ci.id
                        AND confirmed_version.review_status = 'confirmed'
                        AND NOT EXISTS (
                            SELECT 1 FROM card_identity_version revocation
                            WHERE revocation.supersedes_id = confirmed_version.id
                              AND revocation.review_status = 'rejected'
                        )
                      ORDER BY confirmed_version.version DESC LIMIT 1
                  )
                ORDER BY created_at DESC LIMIT 1
            ) sv ON true
            LEFT JOIN LATERAL (
                SELECT id, status, error_message, result_summary, created_at
                FROM valuation_job
                WHERE collection_item_id = ci.id
                ORDER BY created_at DESC LIMIT 1
            ) vj ON true
            LEFT JOIN LATERAL (
                SELECT
                    CASE
                        WHEN result->>'confidence' ~ '^[0-9]+([.][0-9]+)?$'
                        THEN (result->>'confidence')::numeric
                        ELSE NULL
                    END AS recognition_confidence,
                    result AS redemption_recognition,
                    result->>'recognition_status' AS recognition_status,
                    COALESCE(result->'recognition_quality_issues', '[]'::jsonb)
                        AS recognition_quality_issues
                FROM scanner_recognition_job
                WHERE collection_item_id = ci.id AND result IS NOT NULL
                ORDER BY imported_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            ) scanner_recognition ON true
            WHERE ci.owner_id = %s AND {deleted_filter} {item_filter}
            ORDER BY ci.created_at DESC, ci.id DESC
            {pagination}
            """,
            parameters,
        )
        rows = await result.fetchall()
        entries_by_item = await self._owner_ledger_entries(connection, owner_id)
        items: list[PortfolioItem] = []
        for row in rows:
            recognition = row.pop("redemption_recognition", None) or {}
            voucher, status = redemption_identity({"notes": row.pop("redemption_notes", None)})
            if recognition:
                physical = CardRecognitionResult.model_validate(recognition)
                if physical.is_redemption:
                    voucher = True
                    if status == "unknown":
                        status = physical.redemption_status
            row.update(is_redemption=voucher, redemption_status=status)
            position = calculate_position(entries_by_item[row["id"]])
            valuation_compatible = (
                row["fair_value"] is not None and row["valuation_currency"] == row["base_currency"]
            )
            shadow_compatible = (
                row["shadow_fair_value"] is not None
                and row["shadow_valuation_currency"] == row["base_currency"]
            )
            effective_fair = (
                row["fair_value"]
                if valuation_compatible
                else row["shadow_fair_value"]
                if shadow_compatible
                else None
            )
            market_value = (
                effective_fair * position.quantity_held
                if effective_fair is not None
                else None
            )
            items.append(
                PortfolioItem(
                    **{k: v for k, v in row.items() if k != "base_currency"},
                    quantity_held=position.quantity_held,
                    remaining_cost=position.remaining_cost,
                    realized_profit=position.realized_profit,
                    unrealized_profit=(
                        market_value - position.remaining_cost if market_value is not None else None
                    ),
                )
            )
        return items

    async def count_items(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> int:
        deleted_filter = "deleted_at IS NOT NULL" if deleted_only else "deleted_at IS NULL"
        result = await connection.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM collection_item
            WHERE owner_id = %s AND {deleted_filter}
            """,
            (owner_id,),
        )
        row = await result.fetchone()
        return int(row["total"])

    async def quick_update_item(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        payload: PortfolioItemQuickUpdate,
    ) -> None:
        fields = payload.model_fields_set
        if not fields:
            return
        async with connection.transaction():
            selected = await connection.execute(
                """
                SELECT ci.card_definition_id, ci.serial_number, ci.serial_total,
                       u.base_currency
                FROM collection_item ci
                JOIN app_user u ON u.id = ci.owner_id
                WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
                FOR UPDATE
                """,
                (item_id, owner_id),
            )
            item = await selected.fetchone()
            if item is None:
                raise NotFoundError("collection item not found")

            if "is_numbered" in fields:
                await connection.execute(
                    """
                    UPDATE collection_item
                    SET is_numbered = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (payload.is_numbered, item_id),
                )

            if fields & {"serial_number", "serial_total"}:
                serial_number = (
                    payload.serial_number if "serial_number" in fields else item["serial_number"]
                )
                serial_total = (
                    payload.serial_total if "serial_total" in fields else item["serial_total"]
                )
                if serial_number is not None and serial_total is None:
                    raise ConflictError("serial_total is required when serial_number is present")
                if (
                    serial_number is not None
                    and serial_total is not None
                    and serial_number > serial_total
                ):
                    raise ConflictError("serial_number cannot exceed serial_total")
                await connection.execute(
                    """
                    UPDATE collection_item
                    SET serial_number = %s, serial_total = %s,
                        is_numbered = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (serial_number, serial_total, serial_total is not None, item_id),
                )
                await connection.execute(
                    """
                    INSERT INTO valuation_job (collection_item_id, status)
                    VALUES (%s, 'queued')
                    ON CONFLICT (collection_item_id)
                        WHERE status IN ('queued', 'identifying', 'searching', 'calculating')
                    DO NOTHING
                    """,
                    (item_id,),
                )

            definition_updates: list[str] = []
            definition_values: list[object] = []
            if "is_rookie" in fields:
                definition_updates.append("is_rookie = %s")
                definition_values.append(payload.is_rookie)
            if "is_autograph" in fields:
                definition_updates.extend([
                    "is_autograph = %s",
                    "autograph_type = CASE WHEN %s THEN CASE WHEN autograph_type = 'none' THEN 'unknown' ELSE autograph_type END ELSE 'none' END",
                ])
                definition_values.extend([payload.is_autograph, payload.is_autograph])
            if "is_memorabilia" in fields:
                definition_updates.extend([
                    "is_memorabilia = %s",
                    "memorabilia_usage = CASE WHEN %s THEN CASE WHEN memorabilia_usage = 'none' THEN 'unknown' ELSE memorabilia_usage END ELSE 'none' END",
                    "is_patch = CASE WHEN %s THEN is_patch ELSE false END",
                ])
                definition_values.extend([
                    payload.is_memorabilia,
                    payload.is_memorabilia,
                    payload.is_memorabilia,
                ])
            if definition_updates:
                await connection.execute(
                    f"UPDATE card_definition SET {', '.join(definition_updates)}, updated_at = now() WHERE id = %s",
                    (*definition_values, item["card_definition_id"]),
                )

            if "purchase_price" in fields:
                currency = (payload.purchase_currency or item["base_currency"]).upper()
                existing = await connection.execute(
                    """
                    SELECT id FROM card_transaction
                    WHERE collection_item_id = %s AND transaction_type = 'buy'
                    ORDER BY transacted_at, created_at, id LIMIT 1
                    FOR UPDATE
                    """,
                    (item_id,),
                )
                transaction = await existing.fetchone()
                if transaction:
                    await connection.execute(
                        """
                        UPDATE card_transaction
                        SET unit_price = %s, currency = %s
                        WHERE id = %s
                        """,
                        (payload.purchase_price, currency, transaction["id"]),
                    )
                else:
                    await connection.execute(
                        """
                        INSERT INTO card_transaction (
                            collection_item_id, transaction_type, transacted_at,
                            quantity, unit_price, fee_amount, currency, fx_rate_to_base, notes
                        ) VALUES (%s, 'buy', now(), 1, %s, 0, %s, 1, %s)
                        """,
                        (item_id, payload.purchase_price, currency, "从卡片列表初始化买入价"),
                    )

    async def list_deleted_items(
        self, connection: AsyncConnection, owner_id: UUID
    ) -> list[PortfolioItem]:
        return await self.list_items(connection, owner_id, deleted_only=True)

    async def soft_delete_item(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        owner_id: UUID,
        reason: str | None,
    ) -> None:
        result = await connection.execute(
            """
            UPDATE collection_item
            SET deleted_at = COALESCE(deleted_at, now()),
                deleted_reason = CASE
                    WHEN deleted_at IS NULL THEN %s
                    ELSE deleted_reason
                END,
                deleted_by = COALESCE(deleted_by, %s),
                updated_at = now()
            WHERE id = %s AND owner_id = %s
            RETURNING id
            """,
            (reason, owner_id, item_id, owner_id),
        )
        if await result.fetchone() is None:
            raise NotFoundError("collection item not found")
        await connection.execute(
            """
            UPDATE valuation_job
            SET status = 'failed',
                error_message = '卡片已移入回收站',
                finished_at = COALESCE(finished_at, now()),
                updated_at = now()
            WHERE collection_item_id = %s
              AND status IN ('queued', 'identifying', 'searching', 'calculating')
            """,
            (item_id,),
        )

    async def restore_item(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> None:
        result = await connection.execute(
            """
            UPDATE collection_item
            SET deleted_at = NULL, deleted_reason = NULL, deleted_by = NULL, updated_at = now()
            WHERE id = %s AND owner_id = %s
            RETURNING id
            """,
            (item_id, owner_id),
        )
        if await result.fetchone() is None:
            raise NotFoundError("collection item not found")

    async def permanently_delete_item(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> tuple[str | None, str | None]:
        async with connection.transaction():
            item = await connection.execute(
                """
                SELECT image_front_url, image_back_url
                FROM collection_item
                WHERE id = %s AND owner_id = %s AND deleted_at IS NOT NULL
                FOR UPDATE
                """,
                (item_id, owner_id),
            )
            row = await item.fetchone()
            if row is None:
                exists = await connection.execute(
                    "SELECT 1 FROM collection_item WHERE id = %s AND owner_id = %s",
                    (item_id, owner_id),
                )
                if await exists.fetchone():
                    raise ConflictError("collection item must be in trash before permanent deletion")
                raise NotFoundError("collection item not found")
            # Keep the Card Hobby import row as an audit record, but detach it
            # before removing the collection item it originally created.
            await connection.execute(
                """
                UPDATE cardhobby_import_item
                SET collection_item_id = NULL,
                    raw_payload = raw_payload || jsonb_build_object(
                        'collection_item_permanently_deleted_at', now()
                    )
                WHERE collection_item_id = %s AND owner_id = %s
                """,
                (item_id, owner_id),
            )
            await connection.execute(
                "DELETE FROM collection_item WHERE id = %s AND owner_id = %s",
                (item_id, owner_id),
            )
            return row["image_front_url"], row["image_back_url"]

    async def summary(self, connection: AsyncConnection, owner_id: UUID) -> PortfolioSummary:
        user_result = await connection.execute(
            "SELECT base_currency FROM app_user WHERE id = %s", (owner_id,)
        )
        user = await user_result.fetchone()
        if user is None:
            raise NotFoundError("owner not found")
        items = await self.list_items(connection, owner_id)
        return build_portfolio_summary(items, owner_id, user["base_currency"])

    async def item_detail(
        self, connection: AsyncConnection, owner_id: UUID, item_id: UUID
    ) -> PortfolioItemDetail:
        items = await self.list_items(connection, owner_id)
        item = next((candidate for candidate in items if candidate.id == item_id), None)
        if item is None:
            raise NotFoundError("collection item not found")
        transaction_result = await connection.execute(
            """
            SELECT id, transaction_type, transacted_at, quantity, unit_price,
                   fee_amount, currency, fx_rate_to_base, venue, notes
            FROM card_transaction WHERE collection_item_id = %s
            ORDER BY transacted_at DESC, created_at DESC
            """,
            (item_id,),
        )
        valuation_result = await connection.execute(
            """
            SELECT id, valued_at, method, currency, low_value, fair_value,
                   high_value, confidence
            FROM valuation_snapshot WHERE collection_item_id = %s
            ORDER BY valued_at DESC, created_at DESC
            """,
            (item_id,),
        )
        return PortfolioItemDetail(
            item=item,
            transactions=[TransactionView(**row) for row in await transaction_result.fetchall()],
            valuations=[ValuationView(**row) for row in await valuation_result.fetchall()],
        )

    async def _ledger_entries(
        self, connection: AsyncConnection, item_id: UUID
    ) -> list[LedgerEntry]:
        result = await connection.execute(
            """
            SELECT transaction_type, quantity, unit_price, fee_amount, fx_rate_to_base
            FROM card_transaction
            WHERE collection_item_id = %s
            ORDER BY transacted_at, created_at, id
            """,
            (item_id,),
        )
        return [self._to_entry(row) for row in await result.fetchall()]

    async def _owner_ledger_entries(
        self, connection: AsyncConnection, owner_id: UUID
    ) -> defaultdict[UUID, list[LedgerEntry]]:
        result = await connection.execute(
            """
            SELECT t.collection_item_id, t.transaction_type, t.quantity,
                   t.unit_price, t.fee_amount, t.fx_rate_to_base
            FROM card_transaction t
            JOIN collection_item ci ON ci.id = t.collection_item_id
            WHERE ci.owner_id = %s AND ci.deleted_at IS NULL
            ORDER BY t.collection_item_id, t.transacted_at, t.created_at, t.id
            """,
            (owner_id,),
        )
        grouped: defaultdict[UUID, list[LedgerEntry]] = defaultdict(list)
        for row in await result.fetchall():
            grouped[row["collection_item_id"]].append(self._to_entry(row))
        return grouped

    @staticmethod
    def _to_entry(row: dict) -> LedgerEntry:
        return LedgerEntry(
            kind=TransactionKind(row["transaction_type"]),
            quantity=row["quantity"],
            unit_price=row["unit_price"],
            fee_amount=row["fee_amount"],
            fx_rate_to_base=row["fx_rate_to_base"],
        )
