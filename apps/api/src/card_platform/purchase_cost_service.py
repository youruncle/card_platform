import asyncio
import logging
import hashlib
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from card_platform.purchase_cost import OrderCapture, allocate_shipping, evidence_hash, order_errors, score_match, prepare_purchase_recall
from card_platform.purchase_cost_search import build_purchase_cost_search_plan
from card_platform.purchase_cost_images import multi_review_eligible
from card_platform.purchase_cost_bundle import bundle_match, same_bundle_layout
from card_platform.purchase_cost_visual import load_purchase_visual_reviews, purchase_visual_evidence
from card_platform.purchase_cost_inference import (
    inference_key, inference_eligible, apply_inference, title_correction_eligible, TITLE_CORRECTION_CONFLICTS,
)
from card_platform.comparable_image_verifier import (
    ComparableImageVerifier, VisualCandidate, visual_asset_binding, normalize_model_visual_decision,
)
from card_platform.config import get_settings
from card_platform.purchase_cost_knowledge import (
    load_purchase_cost_knowledge, scoped_purchase_cost_knowledge, claim_purchase_observation_attempt,
)
from card_platform.purchase_cost_diagnostics import summarize_candidates
from card_platform.purchase_cost_archive import PurchaseArchive
from card_platform.purchase_cost_catalog import score_from_product
from card_platform.repository import ConflictError, NotFoundError, PortfolioRepository
from card_platform.schemas import TransactionCreate, TransactionType

logger = logging.getLogger(__name__)
FIELD_GATE_KEY = hashlib.sha256(b'purchase-cost-candidate-field-v1').hexdigest()


def field_scope(decision):
    orders = set(decision.get('pending_order_ids') or [])
    refs = {(row['order_id'], row['item_id']) for row in decision.get('pending_item_refs', [])}
    # Order-only records cannot establish which former products were reviewed.
    legacy = set(decision.get('legacy_pending_order_ids') or [])
    if decision.get('scope_version') != 2:
        legacy.update(orders)
    return orders, refs, legacy


def legacy_rebuild_refs(legacy_orders, orders, historical, units):
    """Prove full retained order coverage before replacing an unknown old scope."""
    current = {o['external_order_id']: o for o in orders}
    refs = set()
    for order_id in legacy_orders:
        previous = [o for o in historical if o['external_order_id'] == order_id]
        if not previous or order_id not in current:
            return set()
        lines = current[order_id]['lines']
        seen = {line['item_id'] for order in previous for line in order['lines']}
        if not seen <= {line['item_id'] for line in lines}:
            return set()
        for line in lines:
            rows = [u for u in units if u['external_order_id'] == order_id
                    and u['item_id'] == line['item_id'] and u['bundle_index'] == 0]
            if (line['quantity'] != 1 or not line['image_urls'] or len(rows) != 1
                    or not rows[0]['active']):
                return set()
            refs.add((order_id, line['item_id']))
    return refs


def field_refs(*groups):
    return [dict(order_id=order, item_id=item) for order, item in sorted({
        (row['order_id'], row['item_id']) for group in groups for row in group
        if row.get('order_id') and row.get('item_id')})]


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_value(v) for v in value]
    return value


async def owner_lock(conn, owner_id):
    # Serialize sync, match-confirm and batch steps for one owner; never a global lock.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 68))", (str(owner_id),))


class PurchaseCostService:
    async def incomplete_field(self, conn, owner_id, item_id, order_ids, item_refs=()):
        row = await conn.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                                 (owner_id, item_id, FIELD_GATE_KEY))
        previous = await row.fetchone()
        pending, refs, legacy = field_scope(previous['decision'] if previous else {})
        pending.update(order_ids)
        refs.update((row['order_id'], row['item_id']) for row in item_refs)
        pending.update(order for order, _ in refs)
        await conn.execute('''INSERT INTO cardhobby_cost_visual_inference(owner_id,collection_item_id,fingerprint,decision)
            VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,collection_item_id,fingerprint)
            DO UPDATE SET decision=EXCLUDED.decision,created_at=now()''',
            (owner_id,item_id,FIELD_GATE_KEY,Jsonb(dict(source='cost_candidate_field',status='incomplete',
                scope_version=2, pending_order_ids=sorted(pending), legacy_pending_order_ids=sorted(legacy),
                pending_item_refs=[dict(order_id=order,item_id=item) for order,item in sorted(refs)]))))
        # Partial observations must not become unique matches in the synced worker.
        await conn.execute('''DELETE FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s
            AND decision->>'source'='cost_visual_inference' AND decision->>'verdict' IN ('same','compatible')''', (owner_id,item_id))

    async def sync(self, conn, owner_id, orders):
        from card_platform.purchase_cost_shipping import apply_collect_shipping_policy
        results = []
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            orders = await apply_collect_shipping_policy(conn, owner_id, orders)
            for order in orders:
                errors = order_errors(order)
                digest = evidence_hash(order)
                result = await conn.execute(
                    "SELECT * FROM cardhobby_purchase_order WHERE owner_id=%s AND external_order_id=%s FOR UPDATE",
                    (owner_id, order.external_order_id),
                )
                previous = await result.fetchone()
                if previous and order.fetched_at < previous["fetched_at"]:
                    results.append(
                        dict(order_id=order.external_order_id, status="stale", errors=errors)
                    )
                    continue
                capture = Jsonb(order.model_dump(mode="json"))
                result = await conn.execute(
                    """
                    INSERT INTO cardhobby_purchase_order(owner_id,external_order_id,capture,evidence_hash,errors,fetched_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(owner_id,external_order_id) DO UPDATE SET
                     capture=EXCLUDED.capture, evidence_hash=EXCLUDED.evidence_hash,
                     errors=EXCLUDED.errors, fetched_at=EXCLUDED.fetched_at, updated_at=now(),
                     revision=cardhobby_purchase_order.revision +
                       (cardhobby_purchase_order.evidence_hash<>EXCLUDED.evidence_hash)::integer
                    RETURNING id
                """,
                    (
                        owner_id,
                        order.external_order_id,
                        capture,
                        digest,
                        Jsonb(errors),
                        order.fetched_at,
                    ),
                )
                order_id = (await result.fetchone())["id"]
                await conn.execute(
                    """INSERT INTO cardhobby_order_evidence(order_id,evidence_hash,capture,errors,fetched_at)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(order_id,evidence_hash) DO NOTHING""",
                    (order_id, digest, capture, Jsonb(errors), order.fetched_at),
                )
                await conn.execute(
                    "UPDATE cardhobby_purchase_unit SET active=false WHERE order_id=%s", (order_id,)
                )
                for unit in allocate_shipping(order):
                    await conn.execute(
                        """INSERT INTO cardhobby_purchase_unit
                        (order_id,owner_id,item_id,unit_index,title,unit_price,shipping,active)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(order_id,item_id,unit_index,bundle_index) DO UPDATE SET
                        title=EXCLUDED.title,unit_price=EXCLUDED.unit_price,shipping=EXCLUDED.shipping,active=EXCLUDED.active""",
                        (
                            order_id,
                            owner_id,
                            unit["item_id"],
                            unit["unit_index"],
                            unit["title"],
                            unit["unit_price"],
                            unit["shipping"],
                            not errors,
                        ),
                    )
                if not errors:
                    await conn.execute("""UPDATE cardhobby_purchase_unit SET active=true
                        WHERE order_id=%s AND bundle_index>0 AND bundle_evidence->>'order_hash'=%s""",
                        (order_id, digest))
                await conn.execute("""UPDATE cardhobby_purchase_unit parent SET active=false
                    WHERE parent.order_id=%s AND parent.bundle_index=0 AND EXISTS
                    (SELECT 1 FROM cardhobby_purchase_unit child WHERE child.order_id=parent.order_id
                     AND child.item_id=parent.item_id AND child.bundle_index>0 AND (child.active OR EXISTS (SELECT 1 FROM cardhobby_cost_assignment a WHERE a.unit_id=child.id)))""", (order_id,))
                results.append(
                    dict(
                        order_id=order.external_order_id,
                        status="unchanged"
                        if previous and previous["evidence_hash"] == digest
                        else "synced",
                        errors=errors,
                    )
                )
        return dict(orders=results)

    async def card(self, conn, owner_id, item_id, lock=False):
        result = await conn.execute(
            """SELECT ci.*, p.full_name AS player_name, b.name AS brand_name,
            cs.season_start,cs.season_end,cs.product_name,cs.set_name,
            cd.card_number,cd.insert_name,cd.parallel_name,cd.is_rookie,cd.is_autograph,cd.is_memorabilia,
            cd.is_patch,cd.print_run,
            cd.autograph_type,cd.has_inscription,cd.inscription_text,cd.memorabilia_types,
            cd.memorabilia_usage,cd.memorabilia_player,
            EXISTS(SELECT 1 FROM card_identity_version iv
              WHERE iv.collection_item_id=ci.id AND iv.review_status='confirmed'
              AND NOT EXISTS(SELECT 1 FROM card_identity_version revoked
                WHERE revoked.supersedes_id=iv.id AND revoked.review_status='rejected')) AS has_confirmed_identity,
            u.base_currency,
            (SELECT sj.result FROM scanner_recognition_job sj
             WHERE sj.collection_item_id=ci.id AND sj.owner_id=ci.owner_id AND sj.result IS NOT NULL
             ORDER BY sj.imported_at DESC NULLS LAST,sj.created_at DESC LIMIT 1) AS cost_recognition_evidence
            FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id
            JOIN card_set cs ON cs.id=cd.card_set_id JOIN brand b ON b.id=cs.brand_id
            JOIN player p ON p.id=cd.player_id JOIN app_user u ON u.id=ci.owner_id
            WHERE ci.id=%s AND ci.owner_id=%s AND ci.deleted_at IS NULL"""
            + (" FOR UPDATE OF ci" if lock else ""),
            (item_id, owner_id),
        )
        card = await result.fetchone()
        if not card:
            raise NotFoundError("卡片不存在")
        # A replaced file at the same URL must invalidate a purchase observation.
        settings = get_settings()
        upload_dir = settings.resolved_upload_dir
        card['image_content_hashes'] = {}
        for side in ('front', 'back'):
            image_url = card.get('image_' + side + '_url') or ''
            path = (upload_dir / image_url.removeprefix('/uploads/')).resolve()
            if (image_url.startswith('/uploads/') and path.parent == upload_dir
                    and path.is_file() and path.stat().st_size <= settings.max_image_bytes):
                card['image_content_hashes'][side] = hashlib.sha256(path.read_bytes()).hexdigest()
        from card_platform.domain.recognition_evidence import purchase_identity_view
        return purchase_identity_view(card, card.pop('cost_recognition_evidence', None))

    async def candidates(self, conn, owner_id, item_id, refresh=False, order_scope=None, ignore_field_gate=False):
        card = await self.card(conn, owner_id, item_id)
        rows = await conn.execute(
            """SELECT unit.*,o.capture,o.evidence_hash,o.errors,
            a.collection_item_id AS assigned_item,
            EXISTS(SELECT 1 FROM cardhobby_purchase_unit child
              JOIN cardhobby_cost_assignment ca ON ca.unit_id=child.id
              WHERE child.order_id=unit.order_id AND child.item_id=unit.item_id
                AND child.bundle_index>0) AS assigned_child FROM cardhobby_purchase_unit unit
            JOIN cardhobby_purchase_order o ON o.id=unit.order_id AND o.owner_id=unit.owner_id
            LEFT JOIN cardhobby_cost_assignment a ON a.unit_id=unit.id
            WHERE unit.owner_id=%s"""
            + (" AND o.external_order_id=ANY(%s)" if order_scope is not None else "")
            + " ORDER BY o.external_order_id,unit.item_id,unit.unit_index LIMIT 10001",
            (owner_id, sorted(order_scope)) if order_scope is not None else (owner_id,),
        )
        units = await rows.fetchall()
        if len(units) > 10000:
            raise ConflictError(
                "可用订单超过本次处理上限，已停止自动录入；未将截断结果判断为唯一同款"
            )
        knowledge = await load_purchase_cost_knowledge(conn, card)
        recalled = prepare_purchase_recall(card, knowledge)
        visual_reviews = await load_purchase_visual_reviews(conn, owner_id, item_id, card)
        inference_rows = await conn.execute('SELECT fingerprint,decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND created_at > now()-interval \'7 days\'', (owner_id,item_id))
        inferences = {row['fingerprint']:row['decision'] for row in await inference_rows.fetchall()}
        candidates, rejected = [], []
        archive = PurchaseArchive.configured(get_settings(), owner_id)
        for unit in units:
            order = unit["capture"]
            line = next(
                (line for line in order["lines"] if line["item_id"] == unit["item_id"]), None
            )
            if line is None:
                continue
            decision = inferences.get(inference_key(card,line,unit['evidence_hash']))
            if unit['bundle_index']:
                match = bundle_match(decision, unit['bundle_index'])
                if (unit['bundle_evidence'].get('order_hash') != unit['evidence_hash']
                        or not decision or unit['bundle_evidence'].get('card_count') != decision.get('card_count')
                        or not same_bundle_layout(unit['bundle_evidence'], decision)):
                    continue
            else:
                product = archive.product(line, verify_images=False)
                if not recalled(line) and not decision and not product:
                    continue
                visual = purchase_visual_evidence(card, line, visual_reviews)
                match = score_from_product(card, line, knowledge, product, visual_evidence=visual, scorer=score_match)
                match = apply_inference(card, {**match, "item_id": line["item_id"], "evidence_hash": unit["evidence_hash"]}, decision)
                match = {k: v for k, v in match.items() if k not in {"item_id", "evidence_hash"}}
            if not match["recalled"]:
                continue
            reasons = match["conflicts"] + unit["errors"]
            if not unit["bundle_index"] and unit["assigned_child"]:
                reasons.append("整包已有单卡分摊成本，不能重复分配整包")
            if unit["assigned_item"] and not (refresh and str(unit["assigned_item"]) == str(item_id)):
                reasons.append("该订单件已被库存卡占用")
            if not unit["active"] and not reasons:
                reasons.append("订单件已失效")
            if reasons:
                rejected.append(dict(item_id=unit["item_id"], reasons=reasons,
                                     title=unit['title'], match_reasons=match['reasons'],
                                     identity_conflicts=match['conflicts'],
                                     identity_missing=match['missing'], automatic_identity=match['automatic'],
                                     order_errors=unit['errors'],
                                     allocation_blocked=bool((unit['assigned_item'] and not (refresh and str(unit['assigned_item']) == str(item_id)))
                                         or (not unit['bundle_index'] and unit['assigned_child']))))
                continue
            candidates.append(
                dict(
                    unit_id=str(unit["id"]),
                    evidence_hash=unit["evidence_hash"],
                    order_id=order["external_order_id"],
                    ordered_at=order["ordered_at"],
                    seller=order["seller"],
                    status=order["status"],
                    item_id=unit["item_id"],
                    unit_index=unit["unit_index"],
                    title=unit["title"],
                    image_urls=line["image_urls"],
                    source_url=order["source_url"],
                    item_url=line["item_url"],
                    card_price=str(unit["unit_price"]),
                    shipping=str(unit["shipping"]),
                    shipping_method=order.get("shipping_method", "prepaid"),
                    shipping_basis=order.get("shipping_basis", "order"),
                    total_cost=str(unit["unit_price"] + unit["shipping"]),
                    currency="CNY",
                    allocation_rule=("equal_physical_cards_remainder_item_id_v1"
                                     if order.get("shipping_method") == "sf_collect" else
                                     "equal_bundle_cards_remainder_position_v1" if unit['bundle_index'] else "equal_units_remainder_item_id_v1"),
                    bundle_index=unit['bundle_index'],
                    bundle_evidence=unit['bundle_evidence'],
                    matcher_version="identity-v5-cost-inference",
                    **json_value(match),
                )
            )
        # Missing shipping/physical bundle counts can legitimately produce no
        # allocation units. Keep the raw purchase identity visible as rejected
        # evidence; never invent a zero fee or a confirmable unit for it.
        blocked_orders = await (await conn.execute(
            "SELECT id,capture,errors FROM cardhobby_purchase_order WHERE owner_id=%s AND errors<>'[]'::jsonb"
            + (" AND external_order_id=ANY(%s)" if order_scope is not None else "")
            + " ORDER BY external_order_id LIMIT 10001",
            (owner_id, sorted(order_scope)) if order_scope is not None else (owner_id,),
        )).fetchall()
        observed = {(str(u['order_id']), u['item_id']) for u in units}
        line_count = sum(len(o['capture']['lines']) for o in blocked_orders)
        if len(blocked_orders) > 10000 or line_count > 10000:
            raise ConflictError('待核验订单超过处理上限，未将截断证据判断为唯一同款')
        for order in blocked_orders:
            for line in order['capture']['lines']:
                product = archive.product(line, verify_images=False)
                if (str(order['id']), line['item_id']) in observed or (not recalled(line) and not product):
                    continue
                match = score_from_product(card, line, knowledge, product, scorer=score_match)
                if not match['recalled']:
                    continue
                rejected.append(dict(item_id=line['item_id'], title=line['title'],
                    reasons=match['conflicts'] + order['errors'], match_reasons=match['reasons'],
                    identity_conflicts=match['conflicts'], identity_missing=match['missing'],
                    automatic_identity=match['automatic'], order_errors=order['errors'],
                    allocation_blocked=False))
        candidates.sort(
            key=lambda candidate: (
                -Decimal(candidate["confidence"]),
                candidate["order_id"],
                candidate["item_id"],
                candidate["unit_index"],
            )
        )
        if not ignore_field_gate:
            gate = await conn.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                                      (owner_id, item_id, FIELD_GATE_KEY))
            gate = await gate.fetchone()
            if gate and gate['decision'].get('status') == 'incomplete':
                for candidate in candidates:
                    candidate['automatic'] = False
                    candidate['missing'] = [*candidate['missing'], '购买候选尚未完整核验']
        return dict(
            candidates=candidates,
            rejected=rejected,
            **summarize_candidates(candidates, rejected),
        )

    async def confirm(self, conn, owner_id, item_id, unit_id, digest, automatic=False, order_scope=None, refresh=False):
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            card = await self.card(conn, owner_id, item_id, lock=True)
            existing = await conn.execute(
                "SELECT * FROM cardhobby_cost_assignment WHERE collection_item_id=%s AND owner_id=%s",
                (item_id, owner_id),
            )
            assigned = await existing.fetchone()
            if assigned and not refresh:
                if assigned["unit_id"] == unit_id and assigned["evidence_hash"] == digest:
                    return dict(status="already_recorded", id=str(assigned["id"]))
                raise ConflictError("此卡已有成本来源，禁止重复录入或覆盖")
            entries = await conn.execute(
                "SELECT * FROM card_transaction WHERE collection_item_id=%s FOR UPDATE", (item_id,)
            )
            transactions = await entries.fetchall()
            if transactions and not refresh:
                raise ConflictError(
                    "此卡已有交易或手工成本（含零成本），已保留原记录"
                )
            if card["base_currency"] != "CNY":
                raise ConflictError("账户本位币非 CNY，当前无法自动换算，已跳过录入")
            buys = [row for row in transactions if row['transaction_type'] == 'buy']
            if refresh and transactions and (len(buys) != 1 or buys[0]['quantity'] != 1):
                raise ConflictError("已重新查询，但此卡有多笔买入或数量不为一，无法确定应更新的成本；原记录保留")
            # An explicit selection resolves which purchase belongs to this
            # card. Automatic writes still require the entire field to finish.
            preview = await self.candidates(conn, owner_id, item_id, refresh=refresh,
                                            order_scope=order_scope, ignore_field_gate=not automatic)
            candidate = next(
                (c for c in preview["candidates"] if c["unit_id"] == str(unit_id)), None
            )
            if not candidate or candidate["evidence_hash"] != digest:
                raise ConflictError("候选已被占用或订单金额已变化，请重新识别")
            if not candidate["automatic"]:
                raise ConflictError("卡片信息或订单识别结果已变化，当前无法识别为同款，未录入成本")
            eligible = [c for c in preview["candidates"] if c["automatic"]
                        and (order_scope is None or c["order_id"] in order_scope)]
            if automatic and (len(eligible) != 1 or candidate not in eligible):
                raise ConflictError("同款购买记录不唯一或无法核实，未自动录入")
            purchase = TransactionCreate(
                transaction_type=TransactionType.BUY,
                transacted_at=datetime.combine(datetime.fromisoformat(candidate['ordered_at']).date(), time(), ZoneInfo('Asia/Shanghai')),
                quantity=1, unit_price=Decimal(candidate['card_price']), fee_amount=Decimal(candidate['shipping']),
                currency='CNY', venue='CardHobby', external_order_id=candidate['order_id'],
                notes=(f"卡淘商品 {candidate['item_id']}；包内第{candidate['bundle_index']}张/{candidate['bundle_evidence']['card_count']}张；商品价与运费分别按张均摊"
                       if candidate['bundle_index'] else f"卡淘商品 {candidate['item_id']}；运费按件均分，余分按商品编号/件序分配"),
            )
            if refresh and buys:
                transaction_id = buys[0]['id']
                if assigned and assigned['transaction_id'] != transaction_id:
                    raise ConflictError('成本来源与买入记录不一致，已保留原记录')
                await conn.execute(
                    """INSERT INTO cardhobby_cost_refresh_history
                    (owner_id,collection_item_id,transaction_before,assignment_before,evidence_after)
                    SELECT %s,%s,to_jsonb(t),
                    (SELECT to_jsonb(a) FROM cardhobby_cost_assignment a WHERE a.collection_item_id=%s),%s
                    FROM card_transaction t WHERE t.id=%s""",
                    (owner_id, item_id, item_id, Jsonb(candidate), transaction_id),
                )
                await conn.execute(
                    """UPDATE card_transaction SET unit_price=%s,fee_amount=%s,currency='CNY',
                    fx_rate_to_base=1,venue='CardHobby',external_order_id=%s,transacted_at=%s WHERE id=%s""",
                    (purchase.unit_price, purchase.fee_amount, purchase.external_order_id, purchase.transacted_at, transaction_id),
                )
            else:
                transaction_id = await PortfolioRepository().add_transaction(
                    conn, item_id, owner_id, purchase,
                )
            result = await conn.execute(
                """INSERT INTO cardhobby_cost_assignment
                (owner_id,collection_item_id,unit_id,transaction_id,evidence_hash,card_price,shipping,confidence,evidence,confirmation)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(collection_item_id) DO UPDATE SET
                unit_id=EXCLUDED.unit_id,transaction_id=EXCLUDED.transaction_id,evidence_hash=EXCLUDED.evidence_hash,
                card_price=EXCLUDED.card_price,shipping=EXCLUDED.shipping,confidence=EXCLUDED.confidence,
                evidence=EXCLUDED.evidence,confirmation=EXCLUDED.confirmation
                RETURNING id""",
                (
                    owner_id,
                    item_id,
                    unit_id,
                    transaction_id,
                    digest,
                    Decimal(candidate["card_price"]),
                    Decimal(candidate["shipping"]),
                    Decimal(candidate["confidence"]),
                    Jsonb(candidate),
                    "automatic" if automatic else "user_confirmed",
                ),
            )
            return dict(status="recorded", id=str((await result.fetchone())["id"]))

    async def audit(self, conn, owner_id, item_id):
        await self.card(conn, owner_id, item_id)
        result = await conn.execute(
            """SELECT a.*,o.evidence_hash AS current_hash,o.errors,
            t.unit_price AS current_price,t.fee_amount AS current_shipping,t.currency AS current_currency
            FROM cardhobby_cost_assignment a JOIN cardhobby_purchase_unit unit ON unit.id=a.unit_id
            JOIN cardhobby_purchase_order o ON o.id=unit.order_id
            JOIN card_transaction t ON t.id=a.transaction_id
            WHERE a.owner_id=%s AND a.collection_item_id=%s""",
            (owner_id, item_id),
        )
        row = await result.fetchone()
        if not row:
            return None
        row["source_changed"] = row["current_hash"] != row["evidence_hash"]
        row["manual_override"] = (
            row["current_price"] != row["card_price"]
            or row["current_shipping"] != row["shipping"]
            or row["current_currency"] != "CNY"
        )
        history = await conn.execute(
            """SELECT transaction_before,assignment_before,evidence_after,created_at
            FROM cardhobby_cost_refresh_history WHERE owner_id=%s AND collection_item_id=%s
            ORDER BY created_at DESC LIMIT 100""", (owner_id, item_id),
        )
        row['refresh_history'] = await history.fetchall()
        return row

    async def search_plan(self, conn, owner_id, item_id, refresh=False):
        card = await self.card(conn, owner_id, item_id)
        entries = await conn.execute(
            "SELECT id FROM card_transaction WHERE collection_item_id=%s LIMIT 1", (item_id,)
        )
        label = " ".join(str(card.get(key) or "") for key in ("player_name", "season_start", "product_name")).strip()
        if await entries.fetchone() and not refresh:
            return dict(item_id=str(item_id), label=label, keywords=[], status="skipped",
                        reason="此卡已有购入成本或交易，已保留原记录")
        knowledge = await load_purchase_cost_knowledge(conn, card)
        return dict(item_id=str(item_id), **build_purchase_cost_search_plan(card, knowledge))

    @scoped_purchase_cost_knowledge
    async def resolve_search(self, conn, owner_id, item_id, orders, complete, refresh=False):
        archive_source = False
        if complete and not orders:
            from card_platform.cardhobby_account_api import account_service
            from card_platform.purchase_cost import OrderCapture
            archived = await account_service().archive_orders(conn, owner_id)
            if archived is not None:
                orders = [OrderCapture.model_validate(row) for row in archived]
                archive_source = True
        trace = dict(contract='purchase-cost-stages-v1',
                     trigger=dict(item_id=str(item_id),refresh=refresh),
                     recall=dict(complete=bool(complete),orders=len(orders),
                                 source='complete_private_history' if archive_source else 'live_search'))
        result = await self._resolve_search(conn,owner_id,item_id,orders,complete,refresh=refresh,trace=trace)
        trace['allocation'] = dict(status=result['status'],choices=len(result.get('candidates',[])),
                                  unit_id=(result.get('recorded') or {}).get('unit_id'))
        trace['commit'] = dict(written=result['status']=='recorded',status=result['status'])
        if (get_settings().purchase_cost_worker_enabled
                and any(issue.get('code') == 'multi_photo_pending' for issue in result.get('verification_issues', []))):
            # The user's explicit lookup may need several bounded steps. This
            # is continuation of that lookup, not automatic historical backfill.
            await conn.execute('''INSERT INTO automatic_purchase_cost(item_id,owner_id)
                VALUES(%s,%s) ON CONFLICT(item_id) DO UPDATE SET status='queued',
                attempts=CASE WHEN automatic_purchase_cost.status='queued'
                    THEN automatic_purchase_cost.attempts ELSE 0 END,
                run_after=now(),updated_at=now()
                WHERE automatic_purchase_cost.owner_id=EXCLUDED.owner_id''', (item_id,owner_id))
        return {**result,'trace':trace}

    async def _resolve_search(self, conn, owner_id, item_id, orders, complete, refresh=False, *, trace):
        from card_platform.purchase_cost_shipping import apply_collect_shipping_policy
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            orders = await apply_collect_shipping_policy(conn, owner_id, orders)
            plan = await self.search_plan(conn, owner_id, item_id, refresh=refresh)
            trace["recall"].update(keywords=plan["keywords"],plan_status=plan["status"])
            if plan["status"] == "skipped":
                return dict(status="skipped", reason=plan["reason"], candidates=[], rejected=[])
            if orders:
                await self.sync(conn, owner_id, orders)
            visual_summary = {}
            if complete and orders:
                await self.prepare_bundle_inference(conn,owner_id,item_id,orders)
                visual_summary = await self.prepare_visual_inference(conn,owner_id,item_id,orders,refresh) or {}
            trace['verification'] = visual_summary
            fresh_ids = {order.external_order_id for order in orders}
            preview = await self.candidates(conn, owner_id, item_id, refresh=refresh, order_scope=fresh_ids)
            trace['identity'] = dict(candidates=len(preview['candidates']),rejected=preview['rejected'])
            # A stale cached result must never substitute for a failed/empty live search.
            preview["candidates"] = [c for c in preview["candidates"] if c["order_id"] in fresh_ids]
            fresh_items = {line.item_id for order in orders for line in order.lines}
            preview["rejected"] = [row for row in preview["rejected"] if row["item_id"] in fresh_items]
            eligible = [candidate for candidate in preview["candidates"] if candidate["automatic"]]
            if not complete:
                return dict(status="incomplete", candidates=[], rejected=[],
                            reason="订单搜索尚未完整结束，本次未填写成本")
            if visual_summary.get('issues'):
                # Multiple independently verified purchases can be shown for
                # selection even when other pictures are unresolved. This
                # preview must never authorize an automatic write.
                choices = await self.candidates(conn, owner_id, item_id, refresh=refresh,
                                                order_scope=fresh_ids, ignore_field_gate=True)
                verified_choices = [row for row in choices['candidates'] if row['automatic']]
                if len(verified_choices) > 1:
                    return dict(status="needs_confirmation", candidates=verified_choices, rejected=[],
                                verification_issues=visual_summary['issues'],
                                reason="发现多件同款购买记录，请选择与这张卡对应的一件")
            if visual_summary.get('issues'):
                return dict(status='unmatched',candidates=[],rejected=[],verification_issues=visual_summary['issues'],
                            reason='购买候选的图片核验尚未完整结束，无法确认唯一订单；原成本未修改')
            if len(eligible) > 1:
                return dict(status="needs_confirmation", candidates=eligible, rejected=[],
                            reason="发现多件同款购买记录，请选择与这张卡对应的一件")
            if len(eligible) == 1:
                candidate = eligible[0]
                try:
                    result = await self.confirm(conn, owner_id, item_id, UUID(candidate["unit_id"]), candidate["evidence_hash"], True, fresh_ids, refresh=refresh)
                    return dict(status=result["status"], candidates=[], rejected=[],
                                reason="已按图片与核心信息推测录入，请查看订单快照" if candidate.get('match_method') == 'inferred' else "已重新获取并更新购入成本" if refresh else "已自动录入购入成本", recorded=candidate)
                except ConflictError as error:
                    return dict(status="unmatched" if refresh else "skipped", candidates=[], rejected=[], reason=str(error))
            found = bool(orders or preview["candidates"] or preview["rejected"] or visual_summary.get('attempted')
                         or visual_summary.get('issues') or any(order_errors(order) for order in orders))
            blockers = list(dict.fromkeys(
                [reason for row in preview["candidates"] for reason in row["missing"]]
                + [reason for row in sorted(preview["rejected"],
                    key=lambda row: (-len(row.get('match_reasons', [])), len(row['reasons'])))[:1]
                   for reason in row["reasons"]]
                + [reason for order in orders for reason in order_errors(order)]
            ))
            return dict(status="unmatched" if found else "not_found", candidates=[], rejected=[],
                        verification_issues=visual_summary.get('issues', []),
                        reason=("已读取产品相关购买记录，但图片核验未能确认同款；原成本未修改")
                        if visual_summary.get('issues') else
                        ("最接近的购买记录尚未通过同款核验：" + "；".join(blockers[:3]) + "。原成本未修改") if blockers else
                        "未能从购买记录中识别出对应卡片，本次未填写成本" if found else
                        "已自动搜索购买记录，未找到这张卡的卡淘购入成本")

    async def store_bundle_observation(self, conn, owner_id, card, order, line, decision):
        """Persist trusted server-side image observations, never client-supplied prices."""
        from card_platform.purchase_cost_bundle import bundle_match, split_bundle, same_bundle_layout
        if (not bundle_match(decision, decision.get('matched_index'))['automatic']
                or order_errors(order) or line['quantity'] != 1):
            return False
        if (order.shipping_method == 'sf_collect'
                and order.shipping_card_counts.get(line['item_id']) != decision.get('card_count')):
            return False
        digest = evidence_hash(order)
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            await self.card(conn, owner_id, card['id'], lock=True)
            row = await conn.execute('''SELECT u.* FROM cardhobby_purchase_unit u
                JOIN cardhobby_purchase_order o ON o.id=u.order_id AND o.owner_id=u.owner_id
                WHERE u.owner_id=%s AND o.external_order_id=%s AND o.evidence_hash=%s
                  AND u.item_id=%s AND u.unit_index=0 AND u.bundle_index=0 FOR UPDATE OF u''',
                (owner_id, order.external_order_id, digest, line['item_id']))
            parent = await row.fetchone()
            if not parent:
                return False
            existing = await conn.execute('''SELECT u.bundle_index,u.bundle_evidence,
                EXISTS(SELECT 1 FROM cardhobby_cost_assignment a WHERE a.unit_id=u.id) assigned
                FROM cardhobby_purchase_unit u WHERE u.order_id=%s AND u.item_id=%s''',
                (parent['order_id'], line['item_id']))
            children = await existing.fetchall()
            count = decision['card_count']
            # Conflicting counts must never silently remap an already split physical lot.
            if any((r['bundle_index'] == 0 and r['assigned']) or
                   (r['bundle_index'] > 0 and (r['bundle_evidence'].get('card_count') != count
                    or not same_bundle_layout(r['bundle_evidence'], decision)
                    or (r['assigned'] and r['bundle_evidence'].get('order_hash') != digest)))
                   for r in children):
                return False
            metadata = dict(contract='purchase-bundle-v1', card_count=count, order_hash=digest,
                            bundle_price=str(parent['unit_price']), bundle_shipping=str(parent['shipping']),
                            card_boxes=decision.get('card_boxes') or [],
                            candidate_image_hash=(decision.get('image_hashes') or {}).get('candidate'))
            established = next((r['bundle_evidence'] for r in children if r['bundle_index'] > 0), None)
            if established:
                metadata['card_boxes'] = established.get('card_boxes') or []
                metadata['candidate_image_hash'] = established.get('candidate_image_hash')
            for child in split_bundle(parent['unit_price'], parent['shipping'], count):
                await conn.execute('''INSERT INTO cardhobby_purchase_unit
                    (order_id,owner_id,item_id,unit_index,title,unit_price,shipping,active,bundle_index,bundle_evidence)
                    VALUES (%s,%s,%s,0,%s,%s,%s,true,%s,%s)
                    ON CONFLICT(order_id,item_id,unit_index,bundle_index) DO UPDATE SET
                    unit_price=EXCLUDED.unit_price,shipping=EXCLUDED.shipping,active=true,
                    title=EXCLUDED.title,bundle_evidence=EXCLUDED.bundle_evidence''',
                    (parent['order_id'], owner_id, line['item_id'], line['title'], child['unit_price'],
                     child['shipping'], child['bundle_index'], Jsonb(metadata)))
            await conn.execute('UPDATE cardhobby_purchase_unit SET active=false WHERE id=%s', (parent['id'],))
            await conn.execute('''INSERT INTO cardhobby_cost_visual_inference
                (owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s)
                ON CONFLICT(owner_id,collection_item_id,fingerprint) DO UPDATE SET
                decision=EXCLUDED.decision,created_at=now()''',
                (owner_id, card['id'], inference_key(card,line,digest), Jsonb(decision)))
            return True

    async def prepare_bundle_inference(self, conn, owner_id, item_id, orders):
        from card_platform.purchase_cost import BUNDLE, _phrase_in_title
        from card_platform.purchase_cost_bundle import purchase_player_aliases, bundle_match
        from card_platform.purchase_bundle_verifier import PurchaseBundleVerifier
        from card_platform.domain.card_identity import normalize_text
        card = await self.card(conn, owner_id, item_id)
        knowledge = await load_purchase_cost_knowledge(conn, card)
        aliases = purchase_player_aliases(card['player_name'], knowledge, discovery=True)
        relevant = [(order, line.model_dump(mode='json')) for order in orders if not order_errors(order)
                    for line in order.lines if line.quantity == 1 and BUNDLE.search(line.title)
                    and any(_phrase_in_title(name, normalize_text(line.title)) for name in aliases)]
        if not relevant or len(relevant) > 6:
            return
        verifier = PurchaseBundleVerifier(get_settings())
        for order, line in relevant:
            fingerprint = inference_key(card,line,evidence_hash(order))
            cached = await conn.execute('''SELECT decision FROM cardhobby_cost_visual_inference
                WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s
                  AND created_at>now()-interval '7 days' ''', (owner_id,item_id,fingerprint))
            cached = await cached.fetchone()
            if cached and (bundle_match(cached['decision'],cached['decision'].get('matched_index'))['automatic']
                           or (cached['decision'].get('source') == 'codex_paired_image_review'
                               and cached['decision'].get('verdict') == 'different')):
                continue
            if not claim_purchase_observation_attempt(knowledge, ['bundle',verifier.parallel_model,fingerprint]):
                continue
            try:
                async with asyncio.timeout(65):
                    decision = await verifier.observe(card,line)
                if decision:
                    await self.store_bundle_observation(conn,owner_id,card,order,line,decision)
            except (TimeoutError, ValueError, OSError) as error:
                logger.warning('purchase_bundle_observation_failed type=%s', type(error).__name__)
            except Exception as error:
                # External model/network failure leaves costs untouched and retryable.
                logger.warning('purchase_bundle_observation_failed type=%s', type(error).__name__)

    async def prepare_visual_inference(self, conn, owner_id, item_id, orders, refresh):
        archive = PurchaseArchive.configured(get_settings(), owner_id)
        card = await self.card(conn,owner_id,item_id)
        fresh_ids = {order.external_order_id for order in orders}
        previous_gate = await (await conn.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
            (owner_id,item_id,FIELD_GATE_KEY))).fetchone()
        pending_orders, pending_refs, legacy_orders = field_scope(previous_gate['decision'] if previous_gate else {})
        preview = await self.candidates(conn,owner_id,item_id,refresh=refresh,order_scope=fresh_ids,ignore_field_gate=True)
        unresolved = [dict(order_id=row['order_id'],item_id=row['item_id'],code='candidate_image_missing')
                      for row in preview['candidates'] if inference_eligible(card,row) and not row['image_urls']]
        weak_identities = [dict(unit_id=row['unit_id'],order_id=row['order_id'],item_id=row['item_id'],code='candidate_identity_incomplete')
            for row in preview['candidates'] if not row['automatic'] and not row['conflicts']
            and not inference_eligible(card,row) and not title_correction_eligible(card,row)]
        unresolved.extend(dict(order_id=order.external_order_id,code='order_evidence_incomplete')
            for order in orders if order.external_order_id in pending_orders and order_errors(order))
        rebuilt_refs = set()
        if legacy_orders and archive.fresh():
            from card_platform.cardhobby_account_api import account_service
            snapshots = await account_service().archive_orders(conn, owner_id)
            snapshot_map = {o['external_order_id']: o for o in snapshots or []}
            current_map = {o.external_order_id: o for o in orders}
            if all(o in snapshot_map and o in current_map
                   and evidence_hash(OrderCapture.model_validate(snapshot_map[o])) == evidence_hash(current_map[o])
                   for o in legacy_orders):
                historical = await (await conn.execute('''SELECT e.capture FROM cardhobby_order_evidence e
                    JOIN cardhobby_purchase_order o ON o.id=e.order_id
                    WHERE o.owner_id=%s AND o.external_order_id=ANY(%s)''',
                    (owner_id, sorted(legacy_orders)))).fetchall()
                unit_rows = await (await conn.execute('''SELECT u.item_id,u.active,u.bundle_index,o.external_order_id
                    FROM cardhobby_purchase_unit u JOIN cardhobby_purchase_order o ON o.id=u.order_id
                    WHERE u.owner_id=%s AND o.external_order_id=ANY(%s)''',
                    (owner_id, sorted(legacy_orders)))).fetchall()
                rebuilt_refs = legacy_rebuild_refs(legacy_orders,
                    [o.model_dump(mode='json') for o in orders], [r['capture'] for r in historical], unit_rows)
        rebuilt_orders = {o for o, _ in rebuilt_refs}
        unresolved.extend(dict(order_id=order,code='legacy_candidate_scope_unresolved')
                          for order in sorted(legacy_orders - rebuilt_orders))
        current_refs = {(order.external_order_id,line.item_id) for order in orders for line in order.lines}
        unresolved.extend(dict(order_id=order,item_id=item,code='previous_candidate_scope_not_covered')
                          for order,item in sorted(pending_refs - current_refs))
        candidates = [row for row in preview['candidates'] if row['order_id'] in fresh_ids
                      and inference_eligible(card,row) and row['image_urls']]
        knowledge = await load_purchase_cost_knowledge(conn, card)
        units = await conn.execute('''SELECT u.id,u.item_id,o.external_order_id,
            EXISTS(SELECT 1 FROM cardhobby_cost_assignment a WHERE a.unit_id=u.id
                   AND NOT (%s AND a.collection_item_id=%s)) AS occupied
            FROM cardhobby_purchase_unit u JOIN cardhobby_purchase_order o ON o.id=u.order_id
            WHERE u.owner_id=%s AND o.owner_id=%s AND o.external_order_id=ANY(%s)
              AND u.active AND u.bundle_index=0''',
            (refresh, item_id, owner_id, owner_id, sorted(fresh_ids)))
        unit_rows = await units.fetchall()
        available = {(row['external_order_id'], row['item_id']): row['id']
                     for row in unit_rows if not row['occupied']}
        # Historical scope can be excluded by current negative photo evidence
        # even if another inventory item owns the unit. This map is used only
        # for field review; it never adds an available purchase candidate.
        review_units = {(row['external_order_id'], row['item_id']): row['id']
                        for row in unit_rows if row['occupied']}
        for order in orders:
            if order_errors(order):
                continue
            for captured in order.lines:
                line = captured.model_dump(mode='json')
                ref = (order.external_order_id, line['item_id'])
                unit = available.get(ref)
                review_only = not unit and ref in rebuilt_refs and ref in review_units
                if ref in pending_refs | rebuilt_refs and not unit and not review_only:
                    unresolved.append(dict(order_id=ref[0],item_id=ref[1],code='previous_candidate_unavailable'))
                product = archive.product(line, verify_images=False)
                match = score_from_product(card, line, knowledge, product, scorer=score_match)
                if ref in rebuilt_refs:
                    match = {**match, 'recalled': True, 'automatic': False,
                             'review_evidence': dict(source='legacy_field_rebuild')}
                if ref not in rebuilt_refs and any(row['unit_id'] == str(unit) and row['automatic']
                       and (row.get('visual_inference') or {}).get('source') != 'purchase_multi_image'
                       for row in preview['candidates']):
                    continue
                review_unit = unit or (review_units.get(ref) if review_only else None)
                if (review_unit and line['quantity'] == 1 and line['image_urls']
                        and (multi_review_eligible(card, match) or title_correction_eligible(card, match) or ref in rebuilt_refs)):
                    candidates.append(dict(unit_id=str(review_unit), evidence_hash=evidence_hash(order),
                                           order_id=order.external_order_id, **line, **match,
                                           field_review_only=bool(review_only)))
                elif unit and title_correction_eligible(card, match):
                    unresolved.append(dict(order_id=order.external_order_id,item_id=line['item_id'],code='unsupported_product_candidate'))
                elif (unit and ref in pending_refs and not match['automatic']
                      and set(match['conflicts']) <= TITLE_CORRECTION_CONFLICTS
                      and not inference_eligible(card,match)):
                    # A former suspect cannot disappear simply by losing its
                    # product/name metadata on a later capture.
                    unresolved.append(dict(unit_id=str(unit),order_id=order.external_order_id,
                                           item_id=line['item_id'],code='candidate_identity_incomplete'))
        candidates = list({row['unit_id']: row for row in candidates}.values())
        if candidates or pending_orders:
            # A visual field (or its retry) must account for weak recalled
            # identities too. Leave the existing pure text matching policy alone.
            unresolved.extend(weak_identities)
        # A crowded field is reviewed in durable bounded steps, never truncated
        # and called unique. The observer queues remaining pairs explicitly.
        if not candidates or not card.get('image_front_url'):
            issues = [*unresolved,
                      *([{'code': 'target_image_missing'}] if candidates and not card.get('image_front_url') else [])]
            if not candidates and not issues:
                gate = await (await conn.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                    (owner_id,item_id,FIELD_GATE_KEY))).fetchone()
                if gate:
                    if set(gate['decision'].get('pending_order_ids', [])) <= fresh_ids:
                        # Current text now resolves the full field, without
                        # requiring the formerly missing visual observations.
                        await conn.execute('DELETE FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                            (owner_id,item_id,FIELD_GATE_KEY))
                    else:
                        issues.append(dict(code='previous_candidate_scope_not_covered'))
            if issues:
                await self.incomplete_field(conn,owner_id,item_id,
                    {row['order_id'] for row in candidates} | {row['order_id'] for row in unresolved if row.get('order_id')},
                    field_refs(candidates,unresolved))
            return dict(attempted=0, issues=issues)
        # Only actual review units supersede their weak text identity. Keep
        # unreviewed competitors, including missing photos and quantity > 1;
        # the budget/target-image exits above retain the whole weak field.
        review_unit_ids = {row['unit_id'] for row in candidates}
        unresolved = [row for row in unresolved
                      if row.get('code') != 'candidate_identity_incomplete'
                      or row.get('unit_id') not in review_unit_ids]
        if pending_orders - fresh_ids:
            unresolved.append(dict(code='previous_candidate_scope_not_covered'))
        # Preserve the independently confirmed legacy title-correction path;
        # it is limited to single-photo unnumbered cards and cannot bypass
        # missing serials or true parallel claims.
        if len(candidates) <= 6 and all(
                title_correction_eligible(card,row) and not multi_review_eligible(card,row) for row in candidates):
            if any(len(row['image_urls']) != 1 for row in candidates):
                unresolved.append(dict(code='legacy_title_correction_requires_multi_photo_review'))
                await self.incomplete_field(conn,owner_id,item_id,{r['order_id'] for r in candidates},
                                            field_refs(candidates,unresolved))
                return dict(attempted=0,issues=unresolved)
            return await self.observe_legacy_title_field(conn,owner_id,item_id,card,candidates,unresolved,fresh_ids)
        return await self.observe_purchase_field(conn,owner_id,item_id,card,candidates,unresolved)

    async def observe_legacy_title_field(self, conn, owner_id, item_id, card, candidates, unresolved, fresh_ids):
        try:
            verifier = ComparableImageVerifier(get_settings())
            async with asyncio.timeout(150):
                decisions = await verifier.verify(card['image_front_url'],[
                    VisualCandidate(row['unit_id'],row['title'],row['image_urls'][0]) for row in candidates
                ],requires_parallel_match=bool(card.get('parallel_name') or card.get('serial_total')),
                   target_back_image_url=card.get('image_back_url'),
                   identity={**card, 'collection_item_id': str(item_id)})
        except Exception as error:
            logger.warning('purchase_cost_visual_inference error_type=%s',type(error).__name__)
            await self.incomplete_field(conn,owner_id,item_id,
                {row['order_id'] for row in candidates} | {row['order_id'] for row in unresolved if row.get('order_id')},
                field_refs(candidates,unresolved))
            return dict(attempted=len(candidates), issues=[{'code': 'image_observation_failed', 'type': type(error).__name__}])
        issues = list(unresolved)
        completed = {}
        for row in candidates:
            decision = decisions.get(row['unit_id'])
            if decision is None:
                issues.append(dict(item_id=row['item_id'],code='image_observation_uncertain'))
                continue
            evidence = {**decision.model_dump(mode='json'),'source':'cost_visual_inference',
                        'target_front_url':card['image_front_url'],'target_back_url':card.get('image_back_url'),
                        'candidate_image_url':row['image_urls'][0],'candidate_source_url':row['item_url']}
            expected = visual_asset_binding(card,
                VisualCandidate(row['unit_id'],row['title'],row['image_urls'][0]),b'',None,b'')
            binding = evidence.get('evidence_binding') or {}
            hashes = card.get('image_content_hashes') or {}
            current = (bool(hashes.get('front'))
                       and binding.get('target_front_sha256') == hashes.get('front')
                       and binding.get('target_back_sha256') == hashes.get('back')
                       and all(binding.get(key) == expected[key] for key in
                           ('contract','identity_sha256','candidate_title_sha256','source_url','candidate_image_url'))
                       and isinstance(binding.get('candidate_sha256'),str)
                       and len(binding['candidate_sha256']) == 64
                       and all(char in '0123456789abcdef' for char in binding['candidate_sha256']))
            match = apply_inference(card,row,evidence)
            accepted = match['automatic'] and (match.get('match_method') != 'image_title_correction' or current)
            rejected = (current and decision.verdict == 'different' and decision.confidence >= Decimal('.80')
                        and normalize_model_visual_decision(decision).verdict == 'different')
            if accepted or rejected:
                completed[row['unit_id']] = evidence
            else:
                issues.append(dict(item_id=row['item_id'],code='image_observation_uncertain'))
        gate = await conn.execute('SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                                 (owner_id,item_id,FIELD_GATE_KEY))
        gate = await gate.fetchone()
        if gate and not set(gate['decision'].get('pending_order_ids', [])) <= fresh_ids:
            issues.append(dict(code='previous_candidate_scope_not_covered'))
        if issues:
            await self.incomplete_field(conn,owner_id,item_id,
                {row['order_id'] for row in candidates} | {row['order_id'] for row in unresolved if row.get('order_id')},
                field_refs(candidates,unresolved))
        else:
            await conn.execute('DELETE FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                               (owner_id,item_id,FIELD_GATE_KEY))
        for row in candidates:
            evidence = completed.get(row['unit_id'])
            # Network/model uncertainty must not poison manual retries for seven days.
            if evidence is None:
                continue
            if issues and evidence['verdict'] != 'different':
                continue
            await conn.execute('INSERT INTO cardhobby_cost_visual_inference(owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,collection_item_id,fingerprint) DO UPDATE SET decision=EXCLUDED.decision,created_at=now()',
                               (owner_id,item_id,inference_key(card,row,row['evidence_hash']),Jsonb(evidence)))
        return dict(attempted=len(candidates), issues=issues)

    async def observe_purchase_field(self, conn, owner_id, item_id, card, candidates, unresolved):
        from card_platform.purchase_cost_images import PurchaseImageObserver, ObservationPending
        from card_platform.purchase_cost_archive import PurchaseArchive
        observer = PurchaseImageObserver(get_settings())
        observer.archive = PurchaseArchive.configured(get_settings(), owner_id)
        observer.remaining = 2
        issues = list(unresolved)
        decisions = []
        # Failed candidates retain their attempts, so an unavailable first URL
        # cannot consume every subsequent step and starve the rest of the field.
        ordered = sorted(candidates,key=lambda row:observer.attempt_count(card,row,row['evidence_hash']))
        for row in ordered:
            try:
                async with asyncio.timeout(90):
                    evidence = await observer.observe(card,row,row['evidence_hash'])
                match = apply_inference(card,row,evidence)
                normalized = match.get('visual_inference') or {}
                if (evidence.get('source') == 'purchase_multi_image'
                        and normalized.get('source') == 'purchase_multi_image'
                        and normalized.get('verdict') == 'different'):
                    evidence = normalized
                verified_rejection = (evidence.get('verdict') == 'different'
                    and bool(set(match['conflicts']) - set(row['conflicts']))
                    and match.get('visual_inference') == evidence)
                if row.get('field_review_only') and not verified_rejection:
                    issues.append(dict(order_id=row['order_id'],item_id=row['item_id'],
                                       code='previous_candidate_unavailable'))
                elif not match['automatic'] and not verified_rejection:
                    issues.append(dict(order_id=row['order_id'],item_id=row['item_id'],
                                       code='multi_photo_identity_unconfirmed',reasons=evidence['reasons']))
                decisions.append((row,evidence))
            except ObservationPending:
                issues.append(dict(order_id=row['order_id'],item_id=row['item_id'],
                                   code='multi_photo_pending'))
            except Exception as error:
                issues.append(dict(order_id=row['order_id'],item_id=row['item_id'],
                                   code='multi_photo_observation_failed',type=type(error).__name__))
        # Keep each independently verified observation intact. The field gate
        # below commits atomically with these rows and blocks every automatic
        # path; explicit selection can still show verified choices. Uncertain
        # observations remain diagnostics, never negative cache authority.
        for row,evidence in decisions:
            await conn.execute('''INSERT INTO cardhobby_cost_visual_inference
                (owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s)
                ON CONFLICT(owner_id,collection_item_id,fingerprint) DO UPDATE SET
                decision=EXCLUDED.decision,created_at=now()''',
                (owner_id,item_id,inference_key(card,row,row['evidence_hash']),Jsonb(evidence)))
        if issues:
            await self.incomplete_field(conn,owner_id,item_id,
                {r['order_id'] for r in candidates} | {r['order_id'] for r in unresolved if r.get('order_id')},
                field_refs(candidates,unresolved))
        else:
            await conn.execute('DELETE FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s',
                               (owner_id,item_id,FIELD_GATE_KEY))
        return dict(attempted=len(candidates),issues=issues,
                    new_observations=observer.new_observations,
                    untried_candidates=sum(observer.attempt_count(card,row,row['evidence_hash']) == 0 for row in candidates),
                    observations=[dict(item_id=row['item_id'],verdict=e['verdict'],reasons=e['reasons']) for row,e in decisions])

    async def finish_search_step(self, conn, owner_id, job_id, item_id, orders, complete):
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            job = await self.job(conn, owner_id, job_id)
            if job["source_mode"] != "browser_search":
                raise ConflictError("此任务未启用卡淘实时搜索")
            # A lost response may be retried, but never consumes another item or unit.
            if any(row["item_id"] == str(item_id) for row in job["results"]):
                return job
            if job["status"] not in {"queued", "running"}:
                raise ConflictError("任务已结束或已取消")
            if job["scanned"] >= len(job["item_ids"]) or job["item_ids"][job["scanned"]] != item_id:
                raise ConflictError("任务进度已改变，请刷新后重试")
            try:
                result = await self.resolve_search(conn, owner_id, item_id, orders, complete)
            except (ConflictError, NotFoundError) as error:
                result = dict(status="skipped", reason=str(error))
            outcome = dict(item_id=str(item_id), status=result["status"], reason=result.get("reason", ""))
            succeeded = job["succeeded"] + (result["status"] == "recorded")
            scanned = job["scanned"] + 1
            status = "completed" if succeeded >= job["target"] else "exhausted" if scanned >= len(job["item_ids"]) else "running"
            await conn.execute(
                """UPDATE cardhobby_cost_job SET scanned=%s,succeeded=%s,results=%s,status=%s,
                attempts=0,error=NULL,updated_at=now() WHERE id=%s""",
                (scanned, succeeded, Jsonb([*job["results"], outcome]), status, job_id),
            )
            return await self.job(conn, owner_id, job_id)

    async def create_job(self, conn, owner_id, target, scan_limit, request_key, source_mode="synced"):
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            existing = await conn.execute(
                "SELECT * FROM cardhobby_cost_job WHERE owner_id=%s AND request_key=%s",
                (owner_id, request_key),
            )
            job = await existing.fetchone()
            if job:
                if job["target"] != target or job["scan_limit"] != scan_limit or job["source_mode"] != source_mode:
                    raise ConflictError("幂等键已用于不同任务参数")
                return job
            active = await conn.execute(
                "SELECT id FROM cardhobby_cost_job WHERE owner_id=%s AND status IN ('queued','running')",
                (owner_id,),
            )
            if await active.fetchone():
                raise ConflictError("已有成本识别任务正在运行")
            result = await conn.execute(
                """SELECT ci.id FROM collection_item ci
                WHERE owner_id=%s AND deleted_at IS NULL AND NOT EXISTS
                  (SELECT 1 FROM card_transaction t WHERE t.collection_item_id=ci.id)
                ORDER BY ci.created_at,ci.id LIMIT %s""",
                (owner_id, scan_limit),
            )
            ids = [r["id"] for r in await result.fetchall()]
            result = await conn.execute(
                """INSERT INTO cardhobby_cost_job(owner_id,request_key,target,scan_limit,item_ids,source_mode,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (owner_id, request_key, target, scan_limit, ids, source_mode, "queued" if ids else "exhausted"),
            )
            return await result.fetchone()

    async def job(self, conn, owner_id, job_id, action=None):
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            result = await conn.execute(
                "SELECT * FROM cardhobby_cost_job WHERE id=%s AND owner_id=%s FOR UPDATE",
                (job_id, owner_id),
            )
            job = await result.fetchone()
            if not job:
                raise NotFoundError("任务不存在")
            if action == "cancel" and job["status"] in {"queued", "running"}:
                job["status"] = "cancelled"
            elif action == "search-failed" and job["source_mode"] == "browser_search" and job["status"] in {"queued", "running"}:
                job["status"] = "failed"
                job["error"] = "卡淘搜索中断，请连接卡淘后重试；已完成记录保留"
            elif action == "retry" and job["status"] in {"cancelled", "failed"}:
                active = await conn.execute(
                    "SELECT id FROM cardhobby_cost_job WHERE owner_id=%s AND status IN ('queued','running')",
                    (owner_id,),
                )
                if await active.fetchone():
                    raise ConflictError("请先结束正在运行的任务")
                job["status"] = "queued"
                job["attempts"] = 0
                job["error"] = None
            if action:
                await conn.execute(
                    "UPDATE cardhobby_cost_job SET status=%s,attempts=%s,error=%s,updated_at=now() WHERE id=%s",
                    (job["status"], job["attempts"], job["error"], job_id),
                )
            return job

    async def step(self, conn):
        # One card/segment per lease. Durable model checkpoints survive rollback.
        async with conn.transaction():
            result = await conn.execute(
                "SELECT * FROM cardhobby_cost_job WHERE status IN ('queued','running') AND source_mode='synced' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            job = await result.fetchone()
            if not job:
                return False
            # Try-lock prevents lock-order deadlocks with cancellation (owner -> job).
            lock = await conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,68)) AS locked",
                (str(job["owner_id"]),),
            )
            if not (await lock.fetchone())["locked"]:
                return False
            if job["succeeded"] >= job["target"] or job["scanned"] >= len(job["item_ids"]):
                status = "completed" if job["succeeded"] >= job["target"] else "exhausted"
                await conn.execute(
                    "UPDATE cardhobby_cost_job SET status=%s,updated_at=now() WHERE id=%s",
                    (status, job["id"]),
                )
                return True
            item_id = job["item_ids"][job["scanned"]]
            outcome = dict(item_id=str(item_id))
            try:
                async with conn.transaction():
                    from card_platform.cardhobby_account_api import account_service
                    archived = await account_service().archive_orders(conn, job['owner_id'])
                    if archived is None and PurchaseArchive.configured(get_settings(), job['owner_id']).has_history():
                        # None also means stale, incomplete or changed-account
                        # history. Old owner-scoped DB rows cannot replace it.
                        await conn.execute('''UPDATE cardhobby_cost_job SET status='failed',
                            error='购买历史不可用或已失效，请重新同步完整历史后重试',updated_at=now()
                            WHERE id=%s''', (job['id'],))
                        return True
                    if archived is not None:
                        resolved = await self.resolve_search(conn, job['owner_id'], item_id, [], True)
                        pending = any(i.get('code') == 'multi_photo_pending'
                                      for i in resolved.get('verification_issues', []))
                        if pending:
                            verification = (resolved.get('trace') or {}).get('verification', {})
                            progressed = verification.get('new_observations', 0) or verification.get('untried_candidates', 0)
                            attempts = 0 if progressed else job['attempts']+1
                            await conn.execute('''UPDATE cardhobby_cost_job SET status=%s,attempts=%s,
                                error=%s,updated_at=now() WHERE id=%s''',
                                ('failed' if attempts >= 3 else 'running',attempts,
                                 '候选核验进度已保存，等待继续' if attempts < 3 else '候选资料读取失败，可重试',job['id']))
                            return True
                        outcome.update({k:resolved[k] for k in ('status','reason') if k in resolved})
                        job['succeeded'] += int(resolved['status'] == 'recorded')
                    else:
                        preview = await self.candidates(conn, job["owner_id"], item_id)
                        candidates = [candidate for candidate in preview["candidates"] if candidate["automatic"]]
                        if len(candidates) == 1:
                            candidate = candidates[0]
                            await self.confirm(conn, job["owner_id"], item_id,
                                UUID(candidate["unit_id"]), candidate["evidence_hash"], True)
                            outcome["status"] = "recorded"
                            job["succeeded"] += 1
                        else:
                            outcome.update(
                                status="needs_confirmation" if len(candidates) > 1 else "unmatched" if preview["candidates"] or preview["rejected"] else "not_found",
                                reason="发现多件同款购买记录" if len(candidates) > 1 else "未能识别对应购买记录，本次未填写成本",
                            )
            except (ConflictError, NotFoundError) as error:
                outcome.update(status="skipped", reason=str(error))
            except Exception:
                # No raw error/SQL parameter logging: orders are private financial data.
                attempts = job["attempts"] + 1
                await conn.execute(
                    """UPDATE cardhobby_cost_job SET attempts=%s,error='处理失败，可安全重试',
                    status=%s,updated_at=now() WHERE id=%s""",
                    (attempts, "failed" if attempts >= 3 else "queued", job["id"]),
                )
                logger.warning("purchase_cost_step_failed job=%s attempt=%d", job["id"], attempts)
                return True
            job["results"].append(outcome)
            scanned = job["scanned"] + 1
            status = (
                "completed"
                if job["succeeded"] >= job["target"]
                else "exhausted"
                if scanned >= len(job["item_ids"])
                else "running"
            )
            await conn.execute(
                """UPDATE cardhobby_cost_job SET scanned=%s,succeeded=%s,results=%s,status=%s,
                attempts=0,error=NULL,updated_at=now() WHERE id=%s""",
                (scanned, job["succeeded"], Jsonb(job["results"]), status, job["id"]),
            )
            return True


async def run_cost_worker(database):
    from card_platform.auto_purchase_cost import process_automatic_cost

    service = PurchaseCostService()
    while True:
        try:
            async with database.connection() as conn:
                automatic_worked = await process_automatic_cost(conn, service)
            async with database.connection() as conn:
                worked = await service.step(conn)
            await asyncio.sleep(0.05 if worked or automatic_worked else 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("purchase_cost_worker_unavailable")
            await asyncio.sleep(5)
