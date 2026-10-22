"""Durable public evidence work; capture and approval do not admit a sale.

The caller commits claim/finish transactions before any network or model work.
Approvals are a trusted server operation, never a client-supplied authority set.
"""

import hashlib
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from card_platform.player_prices.image_evidence import _seal
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

    async def evidence(self, c, job_ids):
        if not job_ids:
            return {}
        rows = await (
            await c.execute(
                """SELECT j.*,COALESCE(a.approved_review_sha256s,'{}'::text[]) AS approved_review_sha256s
            FROM player_price_image_job j JOIN player_price_image_current current ON current.job_id=j.id LEFT JOIN LATERAL
              (SELECT array_agg(review_sha256 ORDER BY approved_at,review_sha256) AS approved_review_sha256s
               FROM player_price_image_review_approval WHERE job_id=j.id AND receipt_sha256=j.receipt_sha256) a ON true
            WHERE j.id=ANY(%s) AND j.status='captured'""",
                (job_ids,),
            )
        ).fetchall()
        return {r["id"]: r for r in rows}
