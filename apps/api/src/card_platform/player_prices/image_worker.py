"""Bounded durable public-photo worker. No review approval or price authority."""

import asyncio
import hashlib
import json
import logging

import httpx
from openai import APIConnectionError, APIStatusError

from card_platform.player_prices.image_evidence import make_receipt, _read_bound_receipt
from card_platform.player_prices.image_jobs import ImageJobs
from card_platform.player_prices.image_reader import (
    EvidenceBlobs,
    PriceImageReader,
    PriceImageReadError,
    capture_pair,
)
from card_platform.player_prices.photo_captures import read_bound_capture
from card_platform.player_prices.repository import PriceRepository

log = logging.getLogger(__name__)


class PriceImageWorker:
    def __init__(self, database, settings, *, store=None, reader=None, public_client=None):
        self.database = database
        self.settings = settings
        self.store = store
        self.reader = reader
        self.public_client = public_client
        self.jobs = ImageJobs()

    async def close(self):
        if self.reader is not None:
            await self.reader.close()
        if self.public_client is not None:
            await self.public_client.aclose()

    async def _wake(self, c, player_id):
        subject = await PriceRepository().subject(c, player_id)
        await c.execute(
            """INSERT INTO player_price_state(player_id,requested_at,photo_recheck_requested_at)
            VALUES(%s,clock_timestamp(),clock_timestamp()) ON CONFLICT(player_id) DO UPDATE
            SET requested_at=EXCLUDED.requested_at,photo_recheck_requested_at=EXCLUDED.photo_recheck_requested_at""",
            (subject["id"] if subject else player_id,),
        )

    async def _independent(self, job):
        # Indexes select candidates; archived bytes, never their hashes alone, decide.
        async with self.database.connection() as c:
            context = await self.jobs.source_evidence(c, [job["source_id"]])
        peers = [p for r in context.values() for p in r.get("photo_peers", [])]
        for peer in peers:
            try:
                if peer.get("capture_manifest_sha256"):
                    await asyncio.to_thread(read_bound_capture, peer, self.store.read)
                else:
                    await asyncio.to_thread(
                        _read_bound_receipt, peer["observation"], peer["receipt"], self.store.read
                    )
            except (ValueError, OSError, KeyError, TypeError):
                # Independence cannot be established while a matching anchor is unreadable.
                return False
            return False
        return True

    async def run_once(self):
        if self.settings.player_price_image_daily_call_limit < 2:
            return False
        async with self.database.connection() as c:
            job = await self.jobs.claim(
                c, daily_call_limit=self.settings.player_price_image_daily_call_limit
            )
        if job is None:
            return False
        if self.store is None:
            self.store = EvidenceBlobs(
                f"{self.settings.scanner_job_dir}/.player-price-evidence/blobs"
            )
        if self.public_client is None:
            self.public_client = httpx.AsyncClient(
                timeout=30,
                headers={
                    "Referer": "https://www.cardhobby.com.cn/",
                    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36",
                },
            )
        actual_calls, trace_sha = 0, None

        async def before_request(index):
            nonlocal actual_calls
            async with self.database.connection() as c:
                allowed = await self.jobs.authorize_call(c, job["id"], job["lease_token"], index)
            if allowed:
                actual_calls = index + 1
            return allowed

        async def on_trace(trace):
            nonlocal trace_sha
            trace_sha = await asyncio.to_thread(
                self.store.put,
                json.dumps(
                    dict(contract="price-image-model-trace-v1", **trace),
                    ensure_ascii=False,
                ).encode(),
            )
            async with self.database.connection() as c:
                await self.jobs.record_attempt_trace(c, job["lease_token"], trace_sha)

        try:
            html, images, now = await capture_pair(
                job["observation"], self.store, client=self.public_client
            )
            bindings = [
                dict(
                    url=url,
                    sha256=hashlib.sha256(data).hexdigest(),
                    bytes=len(data),
                    content_type=mime,
                )
                for url, data, mime in images
            ]
            manifest = dict(
                contract="price-public-pair-capture-only-v1",
                status="captured_pair",
                source_id=job["source_id"],
                observation=job["observation"],
                captured_at=now.isoformat(),
                gallery_html_sha256=hashlib.sha256(html.encode()).hexdigest(),
                images=bindings,
                new_model_calls=0,
                not_admitted=True,
            )
            manifest_sha = await asyncio.to_thread(
                self.store.put, json.dumps(manifest, ensure_ascii=False).encode()
            )
            async with self.database.connection() as c:
                await self.jobs.record_photo_capture(c, manifest_sha, blob_reader=self.store.read)
                await self._wake(c, job["player_id"])
            if not await self._independent(job):
                raise ValueError("price_pair_not_independent")
            if self.reader is None:
                self.reader = PriceImageReader(self.settings)
            result, usage = await self.reader.observe(
                [(data, mime) for _, data, mime in images],
                before_request=before_request,
                on_trace=on_trace,
            )
            usage = {k: v for k, v in usage.items() if k != "observations"}
            usage["observation_trace_sha256"] = trace_sha
            receipt = make_receipt(
                job["observation"],
                html,
                images,
                result,
                model=self.reader.model,
                usage=usage,
                now=now,
            )
            async with self.database.connection() as c:
                finished = await self.jobs.finish(
                    c, job["id"], job["lease_token"], receipt, actual_calls=actual_calls
                )
                if finished:
                    await self._wake(c, job["player_id"])
        except asyncio.CancelledError:
            # Keep the lease and conservative debit; restart reclaims only after expiry.
            raise
        except Exception as error:
            if isinstance(error, PriceImageReadError) and str(error) == "price_image_lease_lost":
                return True
            if isinstance(error, PriceImageReadError):
                code = "model_invalid"
                cause = error.__cause__
                if isinstance(cause, (httpx.HTTPError, APIConnectionError)) or (
                    isinstance(cause, APIStatusError)
                    and (cause.status_code >= 500 or cause.status_code == 429)
                ):
                    code = "transport"
            elif isinstance(error, httpx.HTTPError):
                code = "transport"
            elif str(error) in {
                "price_pair_gallery_missing",
                "duplicate_price_images",
                "price_pair_not_independent",
            }:
                code = "insufficient_images"
            elif str(error) in {"photo_bytes", "photo_dimensions_or_format"}:
                code = "unreadable_images"
            else:
                code = "unbound_gallery"
            if actual_calls and trace_sha is None:
                await on_trace(
                    dict(model_calls=actual_calls, observations=[], error_type=type(error).__name__)
                )
            async with self.database.connection() as c:
                await self.jobs.fail(
                    c,
                    job["id"],
                    job["lease_token"],
                    error_code=code,
                    actual_calls=actual_calls,
                    trace_sha256=trace_sha,
                )
        return True

    async def run(self):
        try:
            while True:
                try:
                    worked = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Player price image worker failed; bounded retry")
                    worked = False
                if not worked:
                    await asyncio.sleep(60)
        finally:
            await self.close()


async def run_image_worker(database, settings):
    await PriceImageWorker(database, settings).run()
