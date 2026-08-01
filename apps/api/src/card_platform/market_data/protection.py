import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from card_platform.db import Database
from card_platform.market_data.models import MarketIdentity, ProviderFetchResult
from card_platform.market_data.provider import MarketDataProvider


class SourceLimitedError(RuntimeError):
    pass


def retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return max(0, int(value))
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0, int((parsed - datetime.now(timezone.utc)).total_seconds()))


class ProtectedProvider:
    """Database-backed limiter and circuit breaker; never attempts anti-bot evasion."""

    def __init__(self, database: Database, provider: MarketDataProvider) -> None:
        self.database = database
        self.provider = provider
        self._semaphore = asyncio.Semaphore(1)

    async def fetch_sales(
        self, identity: MarketIdentity, *, since: datetime | None = None
    ) -> ProviderFetchResult:
        async with self._semaphore:
            wait_seconds = await self._begin()
            if wait_seconds:
                await asyncio.sleep(wait_seconds)
            started = monotonic()
            try:
                result = await self.provider.fetch_sales(identity, since=since)
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                retry_after = retry_after_seconds(error.response.headers.get("Retry-After"))
                await self._failure(
                    f"HTTP {status}",
                    duration_ms=int((monotonic() - started) * 1000),
                    status=status,
                    retry_after=retry_after,
                    immediate=status in {403, 429},
                )
                if status in {403, 429}:
                    raise SourceLimitedError(f"{self.provider.code} returned HTTP {status}") from error
                raise
            except Exception as error:
                challenge = any(
                    marker in str(error).casefold()
                    for marker in ("captcha", "验证码", "challenge")
                )
                await self._failure(
                    str(error)[:300],
                    duration_ms=int((monotonic() - started) * 1000),
                    challenge=challenge,
                    immediate=challenge,
                )
                if challenge:
                    raise SourceLimitedError(
                        f"{self.provider.code} presented an anti-bot challenge"
                    ) from error
                raise
            await self._success(int((monotonic() - started) * 1000))
            return result

    async def _begin(self) -> float:
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                """
                SELECT * FROM provider_state WHERE provider_code = %s FOR UPDATE
                """,
                (self.provider.code,),
            )
            state = await result.fetchone()
            if state is None:
                raise SourceLimitedError(f"provider {self.provider.code} is not configured")
            now = datetime.now(timezone.utc)
            blocked_until = max(
                (value for value in (state["circuit_until"], state["retry_after_until"]) if value),
                default=None,
            )
            if state["circuit_state"] == "open" and blocked_until and blocked_until > now:
                raise SourceLimitedError(
                    f"{self.provider.code} circuit open until {blocked_until.isoformat()}"
                )
            if state["circuit_state"] == "open":
                await connection.execute(
                    """
                    UPDATE provider_state SET circuit_state = 'half_open', updated_at = now()
                    WHERE provider_code = %s
                    """,
                    (self.provider.code,),
                )
            if state["active_requests"] >= state["concurrent_limit"]:
                raise SourceLimitedError(f"{self.provider.code} concurrency limit reached")
            wait_seconds = 0.0
            if state["last_request_at"]:
                elapsed = (now - state["last_request_at"]).total_seconds()
                wait_seconds = max(0.0, state["min_interval_ms"] / 1000 - elapsed)
            await connection.execute(
                """
                UPDATE provider_state
                SET active_requests = active_requests + 1,
                    request_count = request_count + 1,
                    last_request_at = now(), updated_at = now()
                WHERE provider_code = %s
                """,
                (self.provider.code,),
            )
            return wait_seconds

    async def _success(self, duration_ms: int) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE provider_state SET
                    active_requests = GREATEST(active_requests - 1, 0),
                    success_count = success_count + 1,
                    consecutive_failures = 0, circuit_state = 'closed',
                    circuit_until = NULL, retry_after_until = NULL,
                    total_duration_ms = total_duration_ms + %s,
                    last_success_at = now(), last_error = NULL, updated_at = now()
                WHERE provider_code = %s
                """,
                (duration_ms, self.provider.code),
            )

    async def _failure(
        self,
        message: str,
        *,
        duration_ms: int,
        status: int | None = None,
        retry_after: int | None = None,
        challenge: bool = False,
        immediate: bool = False,
    ) -> None:
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                "SELECT consecutive_failures, error_budget FROM provider_state "
                "WHERE provider_code = %s FOR UPDATE",
                (self.provider.code,),
            )
            state = await result.fetchone()
            failures = state["consecutive_failures"] + 1
            should_open = immediate or failures >= state["error_budget"]
            backoff = retry_after if retry_after is not None else min(3600, 2 ** failures * 15)
            until = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            await connection.execute(
                """
                UPDATE provider_state SET
                    active_requests = GREATEST(active_requests - 1, 0),
                    consecutive_failures = %s,
                    circuit_state = CASE WHEN %s THEN 'open' ELSE circuit_state END,
                    circuit_opened_at = CASE WHEN %s THEN now() ELSE circuit_opened_at END,
                    circuit_until = CASE WHEN %s THEN %s ELSE circuit_until END,
                    retry_after_until = CASE WHEN %s IN (403, 429) THEN %s ELSE retry_after_until END,
                    http_403_count = http_403_count + CASE WHEN %s = 403 THEN 1 ELSE 0 END,
                    http_429_count = http_429_count + CASE WHEN %s = 429 THEN 1 ELSE 0 END,
                    challenge_count = challenge_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    total_duration_ms = total_duration_ms + %s,
                    last_error = %s, updated_at = now()
                WHERE provider_code = %s
                """,
                (
                    failures,
                    should_open,
                    should_open,
                    should_open,
                    until,
                    status,
                    until,
                    status,
                    status,
                    challenge,
                    duration_ms,
                    message,
                    self.provider.code,
                ),
            )
