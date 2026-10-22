"""Durable public evidence work; capture and approval do not admit a sale.

The caller commits claim/finish transactions before any network or model work.
Approvals are a trusted server operation, never a client-supplied authority set.
"""

import asyncio
import hashlib
import re
from collections import defaultdict
from uuid import uuid4

from psycopg.types.json import Jsonb

from card_platform.player_prices.image_evidence import _seal
from card_platform.player_prices.photo_captures import read_capture, capture_pair_key
from card_platform.player_prices.image_reviews import ImageReview
from card_platform.player_prices.repository import PriceRepository
from card_platform.public_source_catalog import validated_sale


class ImageJobs:
    async def enqueue_candidates(self, c, player_id, observations):
        """A shared search hit must not take another subject's job or abort a batch."""
        verified = [(o, *validated_sale(o)) for o in observations]
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            subject = await PriceRepository().subject(c, player_id)
            members = subject["member_ids"] if subject else [player_id]
            existing = await (
                await c.execute(
                    "SELECT source_id,observation_sha256,player_id FROM player_price_image_job WHERE source_id=ANY(%s)",
                    (["item:" + sale.item_id for _, sale, _ in verified],),
                )
            ).fetchall()
            foreign = {
                (r["source_id"], r["observation_sha256"])
                for r in existing
                if r["player_id"] not in members
            }
            return await self.enqueue(
                c,
                player_id,
                [
                    o
                    for o, sale, _ in verified
                    if ("item:" + sale.item_id, o["raw_row_sha256"]) not in foreign
                ],
            )

    async def enqueue(self, c, player_id, observations, *, priority=0):
        # Validate the complete batch before the first write.
        verified = [(o, *validated_sale(o)) for o in observations]
        ids = []
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            subject = await PriceRepository().subject(c, player_id)
            members = subject["member_ids"] if subject else [player_id]
            player_id = subject["id"] if subject else player_id
            for observation, sale, captured in verified:
                row = await (
                    await c.execute(
                        """INSERT INTO player_price_image_job
                (player_id,source_id,observation_sha256,observation,occurred_at,priority)
                VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source_id,observation_sha256) DO UPDATE
                SET priority=LEAST(player_price_image_job.priority,EXCLUDED.priority),player_id=EXCLUDED.player_id
                WHERE player_price_image_job.player_id=ANY(%s) RETURNING id""",
                        (
                            player_id,
                            "item:" + sale.item_id,
                            observation["raw_row_sha256"],
                            Jsonb(observation),
                            sale.occurred_at,
                            priority,
                            members,
                        ),
                    )
                ).fetchone()
                if row is None:
                    raise ValueError("price_image_subject_conflict")
                current = await (
                    await c.execute(
                        "SELECT * FROM player_price_image_current WHERE source_id=%s",
                        ("item:" + sale.item_id,),
                    )
                ).fetchone()
                if (
                    current
                    and current["last_seen_at"] == captured
                    and current["job_id"] != row["id"]
                ):
                    raise ValueError("price_image_capture_time_conflict")
                await c.execute(
                    """INSERT INTO player_price_image_current(source_id,job_id,last_seen_at) VALUES(%s,%s,%s)
                    ON CONFLICT(source_id) DO UPDATE SET job_id=EXCLUDED.job_id,
                    last_seen_at=GREATEST(player_price_image_current.last_seen_at,EXCLUDED.last_seen_at)
                    WHERE EXCLUDED.last_seen_at>player_price_image_current.last_seen_at
                    OR player_price_image_current.job_id=EXCLUDED.job_id""",
                    ("item:" + sale.item_id, row["id"], captured),
                )
                ids.append(row["id"])
        return list(dict.fromkeys(ids))

    async def claim(self, c, *, daily_call_limit, lease_seconds=300):
        if type(daily_call_limit) is not int or daily_call_limit < 0:
            raise ValueError("invalid_price_image_budget")
        if not 30 <= lease_seconds <= 900:
            raise ValueError("invalid_price_image_lease")
        if daily_call_limit < 2:
            return None
        async with c.transaction():
            # Serialize only short claim transactions, never external I/O.
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            day = "(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date"
            used = await (
                await c.execute(
                    f"SELECT COALESCE(sum(reserved_calls),0) AS n FROM player_price_image_attempt WHERE budget_day={day}"
                )
            ).fetchone()
            if used["n"] + 2 > daily_call_limit:
                return None
            row = await (
                await c.execute(
                    """SELECT j.* FROM player_price_image_job j
                JOIN player_price_image_current current ON current.job_id=j.id
                LEFT JOIN (SELECT player_id,max(last_attempt_at) AS latest
                           FROM player_price_image_job GROUP BY player_id) s USING(player_id)
                WHERE ((j.status IN ('pending','retry') AND j.next_attempt_at<=clock_timestamp())
                   OR (j.status='leased' AND j.lease_until<=clock_timestamp()))
                ORDER BY s.latest NULLS FIRST,j.priority,j.created_at,j.id
                LIMIT 1 FOR UPDATE OF j SKIP LOCKED"""
                )
            ).fetchone()
            if row is None:
                return None
            if row["lease_token"]:
                await c.execute(
                    "UPDATE player_price_image_attempt SET status='expired',finished_at=clock_timestamp() WHERE lease_token=%s AND status='leased'",
                    (row["lease_token"],),
                )
            token = uuid4()
            await c.execute(
                f"INSERT INTO player_price_image_attempt(lease_token,job_id,budget_day,reserved_calls) VALUES(%s,%s,{day},2)",
                (token, row["id"]),
            )
            claimed = await (
                await c.execute(
                    """UPDATE player_price_image_job SET status='leased',lease_token=%s,
                lease_until=LEAST(clock_timestamp()+make_interval(secs=>%s),
                    (date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai')+interval '1 day') AT TIME ZONE 'Asia/Shanghai'),
                last_attempt_at=clock_timestamp(),error_code=NULL
                WHERE id=%s RETURNING *""",
                    (token, lease_seconds, row["id"]),
                )
            ).fetchone()
            return {**claimed, "reserved_calls": 2}

    async def authorize_call(self, c, job_id, token, image_index):
        """Commit immediately before a request; cancelled/expired calls keep their debit."""
        if image_index not in (0, 1):
            raise ValueError("invalid_price_image_call_index")
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            row = await (await c.execute(
                """UPDATE player_price_image_attempt a
                SET actual_calls=COALESCE(actual_calls,0)+1
                FROM player_price_image_job j
                WHERE a.lease_token=%s AND a.job_id=%s AND j.id=a.job_id
                  AND a.status='leased' AND j.status='leased' AND j.lease_token=a.lease_token
                  AND j.lease_until>clock_timestamp()
                  AND a.budget_day=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
                  AND COALESCE(a.actual_calls,0)=%s AND a.reserved_calls>COALESCE(a.actual_calls,0)
                  AND EXISTS(SELECT 1 FROM player_price_image_current current WHERE current.job_id=j.id)
                RETURNING a.lease_token""", (token, job_id, image_index)
            )).fetchone()
            return row is not None

    async def record_attempt_trace(self, c, token, trace_sha256):
        # Audit only: a stale worker cannot change job state, budget or another token.
        if not re.fullmatch("[a-f0-9]{64}", trace_sha256):
            raise ValueError("invalid_price_image_trace")
        await c.execute(
            "UPDATE player_price_image_attempt SET trace_sha256=%s WHERE lease_token=%s",
            (trace_sha256, token),
        )

    async def finish(self, c, job_id, token, receipt, *, actual_calls):
        if type(actual_calls) is not int or not 0 <= actual_calls <= 2:
            raise ValueError("invalid_price_image_call_count")
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            row = await (
                await c.execute(
                    """SELECT * FROM player_price_image_job WHERE id=%s AND status='leased'
                AND lease_token=%s AND lease_until>clock_timestamp()
                AND EXISTS(SELECT 1 FROM player_price_image_current current WHERE current.job_id=player_price_image_job.id) FOR UPDATE""",
                    (job_id, token),
                )
            ).fetchone()
            if row is None:
                return False
            if (
                receipt.get("observation_sha256") != row["observation_sha256"]
                or receipt.get("source_id") != row["source_id"]
                or _seal({k: v for k, v in receipt.items() if k != "receipt_sha256"})
                != receipt.get("receipt_sha256")
            ):
                raise ValueError("price_image_receipt_binding")
            await c.execute(
                """UPDATE player_price_image_job SET status='captured',receipt=%s,receipt_sha256=%s,
                lease_token=NULL,lease_until=NULL WHERE id=%s""",
                (Jsonb(receipt), receipt["receipt_sha256"], job_id),
            )
            await c.execute(
                "UPDATE player_price_image_attempt SET status='captured',actual_calls=%s,finished_at=clock_timestamp() WHERE lease_token=%s",
                (actual_calls, token),
            )
            return True

    async def fail(self, c, job_id, token, *, error_code, actual_calls, trace_sha256=None):
        if error_code not in {
            "transport",
            "model_invalid",
            "insufficient_images",
            "unbound_gallery",
            "unreadable_images",
        }:
            raise ValueError("invalid_price_image_error_code")
        if type(actual_calls) is not int or not 0 <= actual_calls <= 2:
            raise ValueError("invalid_price_image_call_count")
        if (actual_calls and trace_sha256 is None) or (
            trace_sha256 is not None and not re.fullmatch("[a-f0-9]{64}", trace_sha256)
        ):
            raise ValueError("price_image_failure_trace_required")
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            row = await (
                await c.execute(
                    """SELECT id FROM player_price_image_job WHERE id=%s AND status='leased'
                AND lease_token=%s AND lease_until>clock_timestamp()
                AND EXISTS(SELECT 1 FROM player_price_image_current current WHERE current.job_id=player_price_image_job.id) FOR UPDATE""",
                    (job_id, token),
                )
            ).fetchone()
            if row is None:
                return False
            attempts = await (
                await c.execute(
                    "SELECT count(*) AS n FROM player_price_image_attempt WHERE job_id=%s",
                    (job_id,),
                )
            ).fetchone()
            retry = error_code == "transport" and attempts["n"] < 3
            await c.execute(
                """UPDATE player_price_image_job SET status=%s,lease_token=NULL,lease_until=NULL,
                error_code=%s,next_attempt_at=clock_timestamp()+make_interval(secs=>%s) WHERE id=%s""",
                (
                    "retry" if retry else "rejected",
                    error_code,
                    min(300 * 2 ** min(attempts["n"], 6), 21600),
                    job_id,
                ),
            )
            await c.execute(
                """UPDATE player_price_image_attempt SET status='failed',actual_calls=%s,trace_sha256=%s,
                finished_at=clock_timestamp() WHERE lease_token=%s""",
                (actual_calls, trace_sha256, token),
            )
            return True

    async def approve_review(
        self, c, job_id, review_sha256, review_bytes, *, expected_receipt_sha256
    ):
        """Trusted server only; caller already inspected these exact stored bytes."""
        review = ImageReview.model_validate_json(review_bytes)
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            row = await (
                await c.execute(
                    """SELECT * FROM player_price_image_job WHERE id=%s AND status='captured'
                    AND EXISTS(SELECT 1 FROM player_price_image_current current WHERE current.job_id=player_price_image_job.id)""",
                    (job_id,),
                )
            ).fetchone()
            if (
                not row
                or not re.fullmatch("[a-f0-9]{64}", review_sha256)
                or hashlib.sha256(review_bytes).hexdigest() != review_sha256
                or row["receipt_sha256"] != expected_receipt_sha256
                or review.based_on_receipt_sha256 != row["receipt_sha256"]
                or review.observation_sha256 != row["observation_sha256"]
                or {im.image_index for im in review.reviewed_images} != {0, 1}
                or any(
                    im.sha256 != row["receipt"]["images"][im.image_index]["sha256"]
                    for im in review.reviewed_images
                )
            ):
                raise ValueError("price_image_review_binding")
            await c.execute(
                """INSERT INTO player_price_image_review_approval(job_id,review_sha256,receipt_sha256,reviewer_kind)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (job_id, review_sha256, row["receipt_sha256"], review.reviewer_kind),
            )

    async def record_photo_capture(self, c, manifest_sha256, *, blob_reader):
        """Trusted server ingestion. Commit other DB work before reading blobs.

        No network/model calls or price permission. Byte validation precedes
        the short write lock shared with final price commits.
        """
        capture = await asyncio.to_thread(read_capture, manifest_sha256, blob_reader)
        async with c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(8184001)")
            inserted = await (
                await c.execute(
                    """INSERT INTO player_price_photo_capture
                (capture_manifest_sha256,source_id,observation_sha256,observation,captured_at,pair_a,pair_b)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING source_id""",
                    (
                        manifest_sha256,
                        capture["source_id"],
                        capture["observation_sha256"],
                        Jsonb(capture["observation"]),
                        capture["captured_at"],
                        capture["pair_a"],
                        capture["pair_b"],
                    ),
                )
            ).fetchone()
            if inserted:
                # Wake original price owners even if both sales left today's
                # window. Matching indexes are only a notification hint; the
                # actual refresh revalidates every blob before withdrawal.
                owners = await (
                    await c.execute(
                        """SELECT DISTINCT s.player_id FROM player_price_sale s
                    WHERE s.source_id IN (
                      SELECT source_id FROM player_price_photo_capture WHERE pair_a=%s AND pair_b=%s
                      UNION SELECT source_id FROM player_price_image_job WHERE status='captured'
                        AND LEAST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256')=%s
                        AND GREATEST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256')=%s)
                    """,
                        (
                            capture["pair_a"],
                            capture["pair_b"],
                            capture["pair_a"],
                            capture["pair_b"],
                        ),
                    )
                ).fetchall()
                subjects = await PriceRepository().subjects(c)
                roots = {subjects.get(row["player_id"])["id"] for row in owners}
                await c.execute(
                    """INSERT INTO player_price_state(player_id,requested_at,photo_recheck_requested_at)
                    SELECT unnest(%s::uuid[]),clock_timestamp(),clock_timestamp()
                    ON CONFLICT(player_id) DO UPDATE SET requested_at=EXCLUDED.requested_at,
                    photo_recheck_requested_at=EXCLUDED.photo_recheck_requested_at""",
                    (sorted(roots, key=str),),
                )
        return dict(source_id=capture["source_id"], capture_manifest_sha256=manifest_sha256)

    async def source_evidence(self, c, source_ids, *, price_job_ids=()):
        """All source photo history, including sources without a model job."""
        source_ids = sorted(set(source_ids))
        ids = await (
            await c.execute(
                "SELECT job_id FROM player_price_image_current WHERE source_id=ANY(%s)",
                (source_ids,),
            )
        ).fetchall()
        records = await self.evidence(c, [row["job_id"] for row in ids])
        eligible = set(price_job_ids)
        for key, record in records.items():
            record["price_eligible"] = key in eligible
        return await self._photo_context(c, records, source_ids)

    async def evidence(self, c, job_ids, *, include_photo_peers=False):
        if not job_ids:
            return {}
        rows = await (
            await c.execute(
                """SELECT j.*,COALESCE(a.approved_review_sha256s,'{}'::text[]) AS approved_review_sha256s
            FROM player_price_image_job j JOIN player_price_image_current current ON current.job_id=j.id LEFT JOIN LATERAL
              (SELECT array_agg(review_sha256 ORDER BY approved_at,review_sha256) AS approved_review_sha256s
               FROM player_price_image_review_approval WHERE job_id=j.id AND receipt_sha256=j.receipt_sha256) a ON true
            WHERE j.id=ANY(%s) AND (j.status='captured' OR (%s AND EXISTS(
              SELECT 1 FROM player_price_image_job old
              WHERE old.source_id=j.source_id AND old.status='captured')))""",
                (job_ids, include_photo_peers),
            )
        ).fetchall()
        records = {r["id"]: r for r in rows}
        if include_photo_peers:
            # Include pending current jobs with only free historical anchors.
            sources = await (
                await c.execute(
                    "SELECT source_id FROM player_price_image_job WHERE id=ANY(%s)",
                    (job_ids,),
                )
            ).fetchall()
            return await self._photo_context(c, records, [r["source_id"] for r in sources])
        return records

    async def _photo_context(self, c, records, source_ids):
        model_anchors = await (
            await c.execute(
                "SELECT * FROM player_price_image_job WHERE source_id=ANY(%s) AND status='captured'",
                (source_ids,),
            )
        ).fetchall()
        free_anchors = await (
            await c.execute(
                "SELECT * FROM player_price_photo_capture WHERE source_id=ANY(%s)",
                (source_ids,),
            )
        ).fetchall()

        def photo_record(row):
            if row.get("capture_manifest_sha256"):
                return {
                    **row,
                    "id": "photo:" + row["capture_manifest_sha256"],
                    "receipt": None,
                    "receipt_sha256": None,
                    "approved_review_sha256s": [],
                    "price_eligible": False,
                }
            return row

        anchors = [photo_record(r) for r in model_anchors + free_anchors]
        pairs = sorted({pair for r in anchors if (pair := capture_pair_key(r)) is not None})
        peers = []
        if pairs:
            pair_args = ([p[0] for p in pairs], [p[1] for p in pairs])
            model_peers = await (
                await c.execute(
                    """SELECT * FROM player_price_image_job WHERE status='captured' AND
                (LEAST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256'),
                 GREATEST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256'))
                IN (SELECT * FROM unnest(%s::text[],%s::text[]))""",
                    pair_args,
                )
            ).fetchall()
            free_peers = await (
                await c.execute(
                    """SELECT * FROM player_price_photo_capture WHERE (pair_a,pair_b)
                IN (SELECT * FROM unnest(%s::text[],%s::text[]))""",
                    pair_args,
                )
            ).fetchall()
            peers = [photo_record(r) for r in model_peers + free_peers]
        anchors_by_source, peers_by_pair = defaultdict(list), defaultdict(list)
        for anchor in sorted(anchors, key=lambda r: str(r["id"])):
            anchors_by_source[anchor["source_id"]].append(anchor)
        for peer in sorted(peers, key=lambda r: str(r["id"])):
            peers_by_pair[capture_pair_key(peer)].append(peer)
        represented = {r["source_id"] for r in records.values()}
        for source, captures in anchors_by_source.items():
            if source not in represented:
                # A context is never a forged model receipt or an eligible job.
                context = {
                    **captures[0],
                    "receipt": None,
                    "receipt_sha256": None,
                    "approved_review_sha256s": [],
                    "price_eligible": False,
                }
                records[context["id"]] = context
        for row in records.values():
            row["photo_peers"] = [
                {**peer, "photo_anchor": anchor}
                for anchor in anchors_by_source[row["source_id"]]
                for peer in peers_by_pair[capture_pair_key(anchor)]
                if peer["source_id"] != row["source_id"]
            ]
        return records
