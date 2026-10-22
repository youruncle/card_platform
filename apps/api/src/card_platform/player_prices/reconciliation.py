"""Audited derived-data repair; public sale facts and private cards stay intact."""

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from psycopg.types.json import Jsonb

from card_platform.player_prices.admission import POLICY
from card_platform.player_prices.domain import TZ, day_end
from card_platform.player_prices.repository import PriceRepository


def _json(value):
    return json.loads(json.dumps(value, default=str))


def _fingerprint(state):
    state = _json(state)
    # Source capture freshness does not change the accepted sale evidence.
    state["sales"] = [
        {k: v for k, v in row.items() if k != "observed_at"} for row in state["sales"]
    ]
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def _state(c, ids, sources):
    state = {}
    state["sales"] = await (
        await c.execute(
            "SELECT * FROM player_price_sale WHERE player_id=ANY(%s) OR source_id=ANY(%s) ORDER BY source_id",
            (ids, sources),
        )
    ).fetchall()
    state["source_conflicts"] = await (
        await c.execute(
            "SELECT * FROM player_price_source_conflict WHERE source_id=ANY(%s) ORDER BY source_id",
            (sources,),
        )
    ).fetchall()
    for key, table, order in [
        ("baskets", "player_price_basket", "player_id,month"),
        ("snapshots", "player_price_snapshot", "player_id,report_date"),
        ("revisions", "player_price_evidence_revision", "player_id"),
    ]:
        state[key] = await (
            await c.execute(
                f"SELECT * FROM {table} WHERE player_id=ANY(%s) ORDER BY {order}", (ids,)
            )
        ).fetchall()
    return state


async def finalize_reconciliation(c, audit_id):
    """Seal derived reports only within the transaction that made this repair."""
    if audit_id is None:
        return
    audit = await (
        await c.execute(
            "SELECT *,txid_current() AS current_transaction FROM player_price_evidence_audit WHERE id=%s FOR UPDATE",
            (audit_id,),
        )
    ).fetchone()
    if (
        not audit
        or audit["rolled_back_at"] is not None
        or audit["transaction_id"] != audit["current_transaction"]
    ):
        raise ValueError("price_repair_finalize_transaction")
    before = audit["before_state"]
    state = await _state(
        c, [UUID(i) for i in before["affected_player_ids"]], before["scope_source_ids"]
    )
    await c.execute(
        "UPDATE player_price_evidence_audit SET after_fingerprint=%s WHERE id=%s",
        (_fingerprint(state), audit_id),
    )


async def reconcile_prices(
    c, player_id, rows, report_date, *, conflicted_sources=(), photo_reuse_sources=()
):
    async with c.transaction():
        return await _reconcile_prices(
            c,
            player_id,
            rows,
            report_date,
            conflicted_sources=conflicted_sources,
            photo_reuse_sources=photo_reuse_sources,
        )


async def _reconcile_prices(
    c, player_id, rows, report_date, *, conflicted_sources, photo_reuse_sources
):
    repo = PriceRepository()
    subject = await repo.subject(c, player_id)
    if subject:
        player_id = subject["id"]
        members = subject["member_ids"]
    else:
        members = [player_id]
    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,81))", (str(player_id),))
    withdrawn = set(photo_reuse_sources)
    previous = await (
        await c.execute(
            "SELECT * FROM player_price_sale WHERE player_id=ANY(%s) OR source_id=ANY(%s) ORDER BY source_id",
            (members, sorted({r["source_id"] for r in rows} | set(conflicted_sources) | withdrawn)),
        )
    ).fetchall()
    by_source = {row["source_id"]: row for row in previous}
    blocked = set(conflicted_sources)
    for incoming in rows:
        old = by_source.get(incoming["source_id"])
        occurred = incoming["occurred_at"]
        if isinstance(occurred, str):
            occurred = datetime.fromisoformat(occurred)
        if occurred.tzinfo is None:
            raise ValueError("price_sale_date_requires_timezone")
        if old and (
            old["sale_price"] != Decimal(str(incoming["sale_price"]))
            or old["occurred_at"] != occurred
            or old["raw_title"] != incoming["raw_title"]
        ):
            blocked.add(incoming["source_id"])
    flags = await (
        await c.execute(
            "SELECT source_id FROM player_price_source_conflict WHERE source_id=ANY(%s)",
            ([r["source_id"] for r in rows],),
        )
    ).fetchall()
    blocked.update(row["source_id"] for row in flags)
    accepted = [r for r in rows if r["source_id"] not in blocked | withdrawn]
    if any(not r["identity"].get("image_evidence", {}).get("receipt_sha256") for r in accepted):
        raise ValueError("price_image_proof_required")
    affected = list(
        dict.fromkeys(
            [
                *members,
                *[
                    r["player_id"]
                    for r in previous
                    if r["source_id"] in ({x["source_id"] for x in rows} | blocked | withdrawn)
                ],
            ]
        )
    )
    for other_id in list(affected):
        if other_id in members:
            continue
        other = await repo.subject(c, other_id)
        other_root = other["id"] if other else other_id
        locked = (
            await (
                await c.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,81)) AS locked",
                    (str(other_root),),
                )
            ).fetchone()
        )["locked"]
        if not locked:
            raise ValueError("price_repair_scope_busy")
        affected.extend(other["member_ids"] if other else [other_id])
    affected = list(dict.fromkeys(affected))
    scope_sources = list(
        dict.fromkeys(
            [r["source_id"] for r in previous]
            + [r["source_id"] for r in rows]
            + sorted(blocked | withdrawn)
        )
    )
    previous = await (
        await c.execute(
            "SELECT * FROM player_price_sale WHERE player_id=ANY(%s) OR source_id=ANY(%s) ORDER BY source_id",
            (affected, scope_sources),
        )
    ).fetchall()
    scope_sources = list(dict.fromkeys(scope_sources + [r["source_id"] for r in previous]))
    before = await _state(c, affected, scope_sources)
    before.update(affected_player_ids=affected, scope_source_ids=scope_sources)
    if withdrawn:
        before["photo_reuse_withdrawals"] = sorted(withdrawn)
    revision = next((r for r in before["revisions"] if r["player_id"] == player_id), None)
    initial = revision is None or revision["policy"] != POLICY
    for source in sorted(blocked):
        await c.execute(
            "INSERT INTO player_price_source_conflict(source_id) VALUES(%s) ON CONFLICT DO NOTHING",
            (source,),
        )
        await c.execute("UPDATE player_price_sale SET excluded=true WHERE source_id=%s", (source,))
    # A verified photo reuse is an evidence withdrawal, not a change to the
    # source's price/date/title. Include old/other-owner members in this audit
    # and normal report invalidation without inventing source-conflict flags.
    if withdrawn:
        await c.execute(
            "UPDATE player_price_sale SET excluded=true WHERE source_id=ANY(%s)",
            (sorted(withdrawn),),
        )
    end = day_end(report_date)
    await c.execute(
        """UPDATE player_price_sale SET excluded=true WHERE player_id=ANY(%s)
        AND (%s OR (occurred_at>=%s AND occurred_at<%s)) AND NOT(source_id=ANY(%s))""",
        (members, initial, end - timedelta(days=90), end, [r["source_id"] for r in accepted]),
    )
    for row in accepted:
        await c.execute(
            """INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title,excluded)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,false) ON CONFLICT(source_id) DO UPDATE SET
            player_id=EXCLUDED.player_id,card_key=EXCLUDED.card_key,identity=EXCLUDED.identity,
            source_url=EXCLUDED.source_url,excluded=false,observed_at=now()""",
            (
                row["source_id"],
                player_id,
                row["card_key"],
                Jsonb(row["identity"]),
                row["sale_price"],
                row["occurred_at"],
                row["source_url"],
                row["raw_title"],
            ),
        )
    after = await (
        await c.execute(
            "SELECT * FROM player_price_sale WHERE player_id=ANY(%s) OR source_id=ANY(%s) ORDER BY source_id",
            (affected, scope_sources),
        )
    ).fetchall()
    # Pure additions leave previously published reports available on partial
    # collection failures. Only changes to an already usable sale invalidate it.
    old_usable = {r["source_id"]: r for r in previous if not r["excluded"]}
    invalidated = []
    for row in after:
        old = old_usable.get(row["source_id"])
        if old and (
            row["excluded"]
            or any(row[k] != old[k] for k in ("player_id", "card_key", "sale_price", "occurred_at"))
        ):
            invalidated.append(old)
    for row in invalidated:
        # The index compares two seven-day windows one day apart (eight
        # source days in total). A source affects its local day through +7.
        local_day = row["occurred_at"].astimezone(TZ).date()
        owner = await repo.subject(c, row["player_id"])
        owner_ids = owner["member_ids"] if owner else [row["player_id"]]
        await c.execute(
            "DELETE FROM player_price_snapshot WHERE player_id=ANY(%s) AND report_date BETWEEN %s AND %s",
            (owner_ids, local_day, local_day + timedelta(days=7)),
        )
    if initial:
        await c.execute("DELETE FROM player_price_snapshot WHERE player_id=ANY(%s)", (members,))
        await c.execute("DELETE FROM player_price_basket WHERE player_id=ANY(%s)", (members,))
    after_state = await _state(c, affected, scope_sources)
    comparable_before = {k: before[k] for k in after_state}
    changed = initial or _fingerprint(comparable_before) != _fingerprint(after_state)
    audit_id = None
    if changed:
        audit_id = (
            await (
                await c.execute(
                    """INSERT INTO player_price_evidence_audit
            (player_id,policy,initial_rebuild,report_date,before_state,admitted_sources,blocked_sources,after_fingerprint)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'') RETURNING id""",
                    (
                        player_id,
                        POLICY,
                        initial,
                        report_date,
                        Jsonb(_json(before)),
                        Jsonb([r["source_id"] for r in accepted]),
                        Jsonb(sorted(blocked)),
                    ),
                )
            ).fetchone()
        )["id"]
        await c.execute(
            """INSERT INTO player_price_evidence_revision(player_id,policy) VALUES(%s,%s)
            ON CONFLICT(player_id) DO UPDATE SET policy=EXCLUDED.policy,applied_at=now()""",
            (player_id, POLICY),
        )
        await finalize_reconciliation(c, audit_id)
    return dict(
        admitted_count=len(accepted),
        blocked_sources=sorted(blocked),
        withdrawn_sources=sorted(withdrawn),
        changed=changed,
        policy=POLICY,
        audit_id=audit_id,
    )


async def rollback_reconciliation(c, audit_id):
    """Trusted repair command; refuse to overwrite later public price changes."""
    async with c.transaction():
        audit = await (
            await c.execute(
                "SELECT * FROM player_price_evidence_audit WHERE id=%s FOR UPDATE", (audit_id,)
            )
        ).fetchone()
        if audit is None or audit["rolled_back_at"] is not None:
            return False
        before = audit["before_state"]
        ids = [UUID(value) for value in before["affected_player_ids"]]
        sources = before["scope_source_ids"]
        for player in sorted(ids, key=str):
            locked = (
                await (
                    await c.execute(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,81)) AS locked",
                        (str(player),),
                    )
                ).fetchone()
            )["locked"]
            if not locked:
                raise ValueError("price_repair_scope_busy")
        current = await _state(c, ids, sources)
        if _fingerprint(current) != audit["after_fingerprint"]:
            raise ValueError("price_repair_changed_since_audit")
        await c.execute(
            "DELETE FROM player_price_sale WHERE player_id=ANY(%s) OR source_id=ANY(%s)",
            (ids, sources),
        )
        for row in before["sales"]:
            await c.execute(
                """INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title,excluded,observed_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    row["source_id"],
                    row["player_id"],
                    row["card_key"],
                    Jsonb(row["identity"]),
                    row["sale_price"],
                    row["occurred_at"],
                    row["source_url"],
                    row["raw_title"],
                    row["excluded"],
                    row["observed_at"],
                ),
            )
        await c.execute("DELETE FROM player_price_basket WHERE player_id=ANY(%s)", (ids,))
        for row in before["baskets"]:
            await c.execute(
                "INSERT INTO player_price_basket(player_id,month,selected_on,cards,created_at) VALUES(%s,%s,%s,%s,%s)",
                (
                    row["player_id"],
                    row["month"],
                    row["selected_on"],
                    Jsonb(row["cards"]),
                    row["created_at"],
                ),
            )
        await c.execute("DELETE FROM player_price_snapshot WHERE player_id=ANY(%s)", (ids,))
        for row in before["snapshots"]:
            await c.execute(
                "INSERT INTO player_price_snapshot(player_id,report_date,result,computed_at) VALUES(%s,%s,%s,%s)",
                (row["player_id"], row["report_date"], Jsonb(row["result"]), row["computed_at"]),
            )
        await c.execute(
            "DELETE FROM player_price_source_conflict WHERE source_id=ANY(%s)", (sources,)
        )
        for row in before["source_conflicts"]:
            await c.execute(
                "INSERT INTO player_price_source_conflict(source_id,created_at) VALUES(%s,%s)",
                (row["source_id"], row["created_at"]),
            )
        await c.execute(
            "DELETE FROM player_price_evidence_revision WHERE player_id=ANY(%s)", (ids,)
        )
        for revision in before["revisions"]:
            await c.execute(
                "INSERT INTO player_price_evidence_revision(player_id,policy,applied_at) VALUES(%s,%s,%s)",
                (revision["player_id"], revision["policy"], revision["applied_at"]),
            )
        await c.execute(
            "UPDATE player_price_evidence_audit SET rolled_back_at=clock_timestamp() WHERE id=%s",
            (audit_id,),
        )
        return True
