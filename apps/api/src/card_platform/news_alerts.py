"""Public NBA news annotations; deliberately independent from card valuations."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class NewsAlertInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    player_id: UUID
    event_key: str = Field(min_length=1, max_length=200)
    direction: Literal['positive', 'negative']
    headline: str = Field(min_length=1, max_length=500)
    facts: str = Field(min_length=1, max_length=3000)
    attribution: str = Field(min_length=1, max_length=5000)
    confidence: Literal['low', 'medium', 'high']
    source_url: HttpUrl
    published_at: AwareDatetime


class NewsAlertView(NewsAlertInput):
    id: UUID
    created_at: AwareDatetime
    expires_at: AwareDatetime


def validate_freshness(alert: NewsAlertInput, now: datetime) -> None:
    age = now - alert.published_at
    if age < timedelta(0) or age > timedelta(hours=72):
        raise ValueError('新闻发布时间必须在最近 72 小时内且不得在未来')


async def import_alerts(connection, alerts: list[NewsAlertInput]) -> dict:
    """Atomic batch: retries cannot renew an existing event."""
    now = datetime.now(timezone.utc)
    for alert in alerts:
        validate_freshness(alert, now)
    inserted = 0
    async with connection.transaction():
        for alert in alerts:
            player = await (await connection.execute(
                '''SELECT p.id FROM player p WHERE p.id = %s AND EXISTS (
                    SELECT 1 FROM collection_item ci
                    JOIN card_definition cd ON cd.id = ci.card_definition_id
                    WHERE cd.player_id = p.id AND ci.deleted_at IS NULL AND ci.holding_status = 'held')''',
                (alert.player_id,),
            )).fetchone()
            if player is None:
                raise ValueError(f'球员不在活动卡库中: {alert.player_id}')
            result = await connection.execute(
                '''INSERT INTO player_news_alert
                   (player_id,event_key,direction,headline,facts,attribution,
                    confidence,source_url,published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (player_id, event_key) DO NOTHING RETURNING id''',
                (alert.player_id, alert.event_key, alert.direction, alert.headline,
                 alert.facts, alert.attribution, alert.confidence,
                 str(alert.source_url), alert.published_at),
            )
            if await result.fetchone():
                inserted += 1
    return {'inserted': inserted, 'duplicates': len(alerts) - inserted}


async def research_context(connection) -> dict:
    """No account, purchase, image, or holding data may enter search prompts."""
    players = await (await connection.execute('''
        SELECT DISTINCT p.id, p.full_name FROM player p
        JOIN card_definition cd ON cd.player_id = p.id
        JOIN collection_item ci ON ci.card_definition_id = cd.id
        WHERE ci.deleted_at IS NULL AND ci.holding_status = 'held' ORDER BY p.full_name
    ''')).fetchall()
    events = await (await connection.execute('''
        SELECT player_id, event_key, headline, source_url, created_at, expires_at
        FROM player_news_alert ORDER BY created_at DESC
    ''')).fetchall()
    return {'players': players, 'previous_events': events}
