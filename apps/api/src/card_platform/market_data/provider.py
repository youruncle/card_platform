from abc import ABC, abstractmethod
from datetime import datetime

from card_platform.market_data.models import MarketIdentity, ProviderFetchResult


class MarketDataProvider(ABC):
    """Authorized market-data boundary. Business code depends only on this contract."""

    code: str

    @abstractmethod
    async def fetch_sales(
        self,
        identity: MarketIdentity,
        *,
        since: datetime | None = None,
    ) -> ProviderFetchResult:
        """Return incremental sale evidence without deleting previously stored evidence."""
