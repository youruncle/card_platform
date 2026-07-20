import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True, slots=True)
class MetricRecord:
    season: str
    scope: str
    metrics: dict[str, float | int | str | None]
    external_id: str


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_type: str
    occurred_at: datetime
    headline: str
    summary: str | None
    source_url: str | None
    external_id: str


class NBAStatsProvider:
    code = "nba_stats"

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def fetch(self, nba_person_id: int) -> list[MetricRecord]:
        headers = {
            "User-Agent": "Mozilla/5.0 CardAlpha/1.0",
            "Referer": "https://www.nba.com/",
            "Origin": "https://www.nba.com",
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.get(
                f"{self.base_url}/playercareerstats",
                params={"PlayerID": nba_person_id, "PerMode": "PerGame"},
            )
            response.raise_for_status()
        return self.parse(response.json(), nba_person_id)

    @staticmethod
    def parse(payload: dict, nba_person_id: int) -> list[MetricRecord]:
        sets = payload.get("resultSets") or payload.get("resultSet") or []
        if isinstance(sets, dict):
            sets = [sets]
        target = next(
            (item for item in sets if item.get("name") == "SeasonTotalsRegularSeason"), None
        )
        if not target:
            return []
        headers = target.get("headers", [])
        records: list[MetricRecord] = []
        for raw in target.get("rowSet", []):
            row = dict(zip(headers, raw, strict=False))
            season = str(row.get("SEASON_ID", "unknown"))
            metrics = {
                "team": row.get("TEAM_ABBREVIATION"),
                "gp": row.get("GP"),
                "min": row.get("MIN"),
                "pts": row.get("PTS"),
                "reb": row.get("REB"),
                "ast": row.get("AST"),
                "stl": row.get("STL"),
                "blk": row.get("BLK"),
                "fg_pct": row.get("FG_PCT"),
                "fg3_pct": row.get("FG3_PCT"),
                "ft_pct": row.get("FT_PCT"),
            }
            records.append(
                MetricRecord(
                    season=season,
                    scope="season",
                    metrics=metrics,
                    external_id=f"{nba_person_id}:{season}:{row.get('TEAM_ID', '')}",
                )
            )
        return records


class RSSProvider:
    def __init__(
        self, code: str, url_template: str, event_type: str, timeout: float = 15.0
    ) -> None:
        self.code = code
        self.url_template = url_template
        self.event_type = event_type
        self.timeout = timeout

    async def fetch(self, player_name: str) -> list[EventRecord]:
        url = self.url_template.format(player=quote_plus(player_name))
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        return self.parse(response.text)

    def parse(self, payload: str) -> list[EventRecord]:
        root = ElementTree.fromstring(payload)
        records: list[EventRecord] = []
        for item in root.findall(".//item")[:20]:
            headline = (item.findtext("title") or "").strip()
            if not headline:
                continue
            link = (item.findtext("link") or "").strip() or None
            summary = (item.findtext("description") or "").strip() or None
            published = item.findtext("pubDate")
            occurred_at = _parse_rss_date(published)
            source_key = link or f"{headline}:{occurred_at.isoformat()}"
            external_id = hashlib.sha256(source_key.encode()).hexdigest()
            records.append(
                EventRecord(
                    event_type=self.event_type,
                    occurred_at=occurred_at,
                    headline=headline,
                    summary=summary,
                    source_url=link,
                    external_id=external_id,
                )
            )
        return records


def _parse_rss_date(value: str | None) -> datetime:
    if value:
        from email.utils import parsedate_to_datetime

        try:
            parsed = parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)
