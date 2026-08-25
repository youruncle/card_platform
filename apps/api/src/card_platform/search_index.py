import asyncio
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SearchIndexHit:
    provider: str
    query: str
    title: str
    url: str
    snippet: str
    result_date: str | None = None


class SearchIndexResearch:
    """Low-volume, API-backed search-index discovery with auditable snippets."""

    def __init__(
        self,
        *,
        brave_api_key: str | None = None,
        serpapi_key: str | None = None,
        timeout: float = 12.0,
        max_queries: int = 7,
        concurrency: int = 2,
    ) -> None:
        self.brave_api_key = brave_api_key
        self.serpapi_key = serpapi_key
        self.timeout = timeout
        self.max_queries = max(1, min(max_queries, 12))
        self.concurrency = max(1, min(concurrency, 4))

    @property
    def enabled(self) -> bool:
        return bool(self.brave_api_key or self.serpapi_key)

    async def search(self, queries: list[str]) -> tuple[list[SearchIndexHit], list[str]]:
        selected = list(dict.fromkeys(query.strip() for query in queries if query.strip()))[
            : self.max_queries
        ]
        providers = []
        if self.brave_api_key:
            providers.append(("brave", self._search_brave))
        if self.serpapi_key:
            providers.append(("serpapi", self._search_serpapi))
        if not selected or not providers:
            return [], []
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run(client: httpx.AsyncClient, provider: str, search, query: str):
            async with semaphore:
                try:
                    return await search(client, query), None
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    return [], f"{provider}: {query}: {str(error)[:160]}"

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        ) as client:
            results = await asyncio.gather(
                *(
                    run(client, provider, search, query)
                    for query in selected
                    for provider, search in providers
                )
            )
        hits = [hit for batch, _ in results for hit in batch]
        errors = [error for _, error in results if error]
        deduplicated: dict[tuple[str, str, str], SearchIndexHit] = {}
        for hit in hits:
            key = (hit.provider, hit.url, hit.snippet)
            deduplicated.setdefault(key, hit)
        return list(deduplicated.values()), errors

    async def _search_brave(
        self, client: httpx.AsyncClient, query: str
    ) -> list[SearchIndexHit]:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": query,
                "count": 20,
                "country": "us",
                "search_lang": "en",
                "safesearch": "off",
            },
            headers={"X-Subscription-Token": self.brave_api_key},
        )
        response.raise_for_status()
        results = response.json().get("web", {}).get("results", [])
        return [
            SearchIndexHit(
                provider="brave",
                query=query,
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("description") or ""),
                result_date=item.get("page_age"),
            )
            for item in results
            if item.get("url")
        ]

    async def _search_serpapi(
        self, client: httpx.AsyncClient, query: str
    ) -> list[SearchIndexHit]:
        response = await client.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google",
                "q": query,
                "hl": "en",
                "gl": "us",
                "num": 20,
                "api_key": self.serpapi_key,
            },
        )
        response.raise_for_status()
        results = response.json().get("organic_results", [])
        return [
            SearchIndexHit(
                provider="serpapi",
                query=query,
                title=str(item.get("title") or ""),
                url=str(item.get("link") or ""),
                snippet=str(item.get("snippet") or ""),
                result_date=item.get("date"),
            )
            for item in results
            if item.get("link")
        ]
