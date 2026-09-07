from unittest.mock import AsyncMock

import pytest

from card_platform.market_data.protection import (
    ProtectedProvider,
    ProviderConcurrencyBusy,
    SourceLimitedError,
)


class StubProvider:
    code = "stub"


@pytest.mark.asyncio
async def test_reserve_request_waits_for_transient_database_lease(monkeypatch) -> None:
    protected = ProtectedProvider(object(), StubProvider())  # type: ignore[arg-type]
    protected._begin = AsyncMock(  # type: ignore[method-assign]
        side_effect=[ProviderConcurrencyBusy("busy"), 0.75]
    )
    monkeypatch.setattr("card_platform.market_data.protection.asyncio.sleep", AsyncMock())

    assert await protected._reserve_request(timeout_seconds=1) == 0.75
    assert protected._begin.await_count == 2


@pytest.mark.asyncio
async def test_reserve_request_keeps_circuit_errors_immediate() -> None:
    protected = ProtectedProvider(object(), StubProvider())  # type: ignore[arg-type]
    protected._begin = AsyncMock(  # type: ignore[method-assign]
        side_effect=SourceLimitedError("circuit open")
    )

    with pytest.raises(SourceLimitedError, match="circuit open"):
        await protected._reserve_request(timeout_seconds=1)

    assert protected._begin.await_count == 1
