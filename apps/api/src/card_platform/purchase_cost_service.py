import asyncio
import logging
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from card_platform.purchase_cost import allocate_shipping, evidence_hash, order_errors, score_match
from card_platform.purchase_cost_search import build_purchase_cost_search_plan
from card_platform.purchase_cost_visual import load_purchase_visual_reviews, purchase_visual_evidence
from card_platform.purchase_cost_inference import inference_key, inference_eligible, apply_inference
from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualCandidate
from card_platform.config import get_settings
from card_platform.knowledge import KnowledgeResolver
from card_platform.repository import ConflictError, NotFoundError, PortfolioRepository
from card_platform.schemas import TransactionCreate, TransactionType

logger = logging.getLogger(__name__)


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
    async def sync(self, conn, owner_id, orders):
        results = []
        async with conn.transaction():
            await owner_lock(conn, owner_id)
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
                        ON CONFLICT(order_id,item_id,unit_index) DO UPDATE SET
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
            u.base_currency
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
        return card

    async def candidates(self, conn, owner_id, item_id, refresh=False):
        card = await self.card(conn, owner_id, item_id)
        rows = await conn.execute(
            """SELECT unit.*,o.capture,o.evidence_hash,o.errors,
            a.collection_item_id AS assigned_item FROM cardhobby_purchase_unit unit
            JOIN cardhobby_purchase_order o ON o.id=unit.order_id AND o.owner_id=unit.owner_id
            LEFT JOIN cardhobby_cost_assignment a ON a.unit_id=unit.id
            WHERE unit.owner_id=%s ORDER BY o.external_order_id,unit.item_id,unit.unit_index LIMIT 10001""",
            (owner_id,),
        )
        units = await rows.fetchall()
        if len(units) > 10000:
            raise ConflictError(
                "可用订单超过本次处理上限，已停止自动录入；未将截断结果判断为唯一同款"
            )
        snapshot = await conn.execute(
            "SELECT kv.snapshot FROM knowledge_workspace kw LEFT JOIN knowledge_version kv ON kv.id=kw.published_version_id WHERE kw.name='Card Platform'"
        )
        knowledge_row = await snapshot.fetchone()
        knowledge = KnowledgeResolver(knowledge_row["snapshot"] if knowledge_row else None)
        visual_reviews = await load_purchase_visual_reviews(conn, owner_id, item_id, card)
        inference_rows = await conn.execute('SELECT fingerprint,decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND created_at > now()-interval \'7 days\'', (owner_id,item_id))
        inferences = {row['fingerprint']:row['decision'] for row in await inference_rows.fetchall()}
        candidates, rejected = [], []
        for unit in units:
            order = unit["capture"]
            line = next(
                (line for line in order["lines"] if line["item_id"] == unit["item_id"]), None
            )
            if line is None:
                continue
            visual = purchase_visual_evidence(card, line, visual_reviews)
            match = score_match(card, line, knowledge, visual_evidence=visual)
            match = apply_inference(card, match, inferences.get(inference_key(card,line,unit['evidence_hash'])))
            if not match["recalled"]:
                continue
            reasons = match["conflicts"] + unit["errors"]
            if unit["assigned_item"] and not (refresh and str(unit["assigned_item"]) == str(item_id)):
                reasons.append("该订单件已被库存卡占用")
            if not unit["active"] and not reasons:
                reasons.append("订单件已失效")
            if reasons:
                rejected.append(dict(item_id=unit["item_id"], reasons=reasons))
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
                    total_cost=str(unit["unit_price"] + unit["shipping"]),
                    currency="CNY",
                    allocation_rule="equal_units_remainder_item_id_v1",
                    matcher_version="identity-v5-cost-inference",
                    **json_value(match),
                )
            )
        candidates.sort(
            key=lambda candidate: (
                -Decimal(candidate["confidence"]),
                candidate["order_id"],
                candidate["item_id"],
                candidate["unit_index"],
            )
        )
        return dict(
            candidates=candidates,
            rejected=rejected,
            reason="未找到可用购买记录，已保留空成本"
            if not candidates
            else "已找到购买记录，系统将继续核对同款身份",
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
            preview = await self.candidates(conn, owner_id, item_id, refresh=refresh)
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
                notes=f"卡淘商品 {candidate['item_id']}；运费按件均分，余分按商品编号/件序分配",
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
        snapshot = await conn.execute(
            "SELECT kv.snapshot FROM knowledge_workspace kw LEFT JOIN knowledge_version kv ON kv.id=kw.published_version_id WHERE kw.name='Card Platform'"
        )
        row = await snapshot.fetchone()
        knowledge = KnowledgeResolver(row["snapshot"] if row else None)
        return dict(item_id=str(item_id), **build_purchase_cost_search_plan(card, knowledge))

    async def resolve_search(self, conn, owner_id, item_id, orders, complete, refresh=False):
        async with conn.transaction():
            await owner_lock(conn, owner_id)
            plan = await self.search_plan(conn, owner_id, item_id, refresh=refresh)
            if plan["status"] == "skipped":
                return dict(status="skipped", reason=plan["reason"], candidates=[], rejected=[])
            if orders:
                await self.sync(conn, owner_id, orders)
            if complete and orders:
                await self.prepare_visual_inference(conn,owner_id,item_id,orders,refresh)
            preview = await self.candidates(conn, owner_id, item_id, refresh=refresh)
            fresh_ids = {order.external_order_id for order in orders}
            # A stale cached result must never substitute for a failed/empty live search.
            preview["candidates"] = [c for c in preview["candidates"] if c["order_id"] in fresh_ids]
            fresh_items = {line.item_id for order in orders for line in order.lines}
            preview["rejected"] = [row for row in preview["rejected"] if row["item_id"] in fresh_items]
            eligible = [candidate for candidate in preview["candidates"] if candidate["automatic"]]
            if not complete:
                return dict(status="incomplete", candidates=[], rejected=[],
                            reason="订单搜索尚未完整结束，本次未填写成本")
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
            found = bool(preview["candidates"] or preview["rejected"] or any(order_errors(order) for order in orders))
            blockers = list(dict.fromkeys(
                [reason for row in preview["candidates"] for reason in row["missing"]]
                + [reason for row in preview["rejected"] for reason in row["reasons"]]
                + [reason for order in orders for reason in order_errors(order)]
            ))
            return dict(status="unmatched" if found else "not_found", candidates=[], rejected=[],
                        reason=("已读取购买记录，但尚未通过同款核验：" + "；".join(blockers[:3]) + "。原成本未修改") if blockers else
                        "未能从购买记录中识别出对应卡片，本次未填写成本" if found else
                        "已自动搜索购买记录，未找到这张卡的卡淘购入成本")

    async def prepare_visual_inference(self, conn, owner_id, item_id, orders, refresh):
        card = await self.card(conn,owner_id,item_id)
        preview = await self.candidates(conn,owner_id,item_id,refresh=refresh)
        fresh_ids = {order.external_order_id for order in orders}
        candidates = [row for row in preview['candidates'] if row['order_id'] in fresh_ids
                      and inference_eligible(card,row) and row['image_urls'] and not row.get('visual_inference')]
        # Do not partially compare a crowded field and call the first one unique.
        if not candidates or len(candidates)>6 or not card.get('image_front_url'):
            return
        try:
            verifier = ComparableImageVerifier(get_settings())
            async with asyncio.timeout(65):
                decisions = await verifier.verify(card['image_front_url'],[
                    VisualCandidate(row['unit_id'],row['title'],row['image_urls'][0]) for row in candidates
                ],requires_parallel_match=bool(card.get('parallel_name') or card.get('serial_total')),
                   target_back_image_url=card.get('image_back_url'))
        except Exception as error:
            logger.warning('purchase_cost_visual_inference error_type=%s',type(error).__name__)
            return
        for row in candidates:
            decision = decisions.get(row['unit_id'])
            # Network/model uncertainty must not poison manual retries for seven days.
            if decision is None or decision.verdict == 'uncertain':
                continue
            evidence = {**decision.model_dump(mode='json'),'source':'cost_visual_inference',
                        'target_front_url':card['image_front_url'],'target_back_url':card.get('image_back_url'),
                        'candidate_image_url':row['image_urls'][0],'candidate_source_url':row['item_url']}
            await conn.execute('INSERT INTO cardhobby_cost_visual_inference(owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s) ON CONFLICT(owner_id,collection_item_id,fingerprint) DO UPDATE SET decision=EXCLUDED.decision,created_at=now()',
                               (owner_id,item_id,inference_key(card,row,row['evidence_hash']),Jsonb(evidence)))

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
        # The job row lock is held for one card only. No external I/O inside the transaction.
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
                    preview = await self.candidates(conn, job["owner_id"], item_id)
                    candidates = [candidate for candidate in preview["candidates"] if candidate["automatic"]]
                    if len(candidates) == 1:
                        candidate = candidates[0]
                        await self.confirm(
                            conn,
                            job["owner_id"],
                            item_id,
                            UUID(candidate["unit_id"]),
                            candidate["evidence_hash"],
                            True,
                        )
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
    service = PurchaseCostService()
    while True:
        try:
            async with database.connection() as conn:
                worked = await service.step(conn)
            await asyncio.sleep(0.05 if worked else 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("purchase_cost_worker_unavailable")
            await asyncio.sleep(5)
