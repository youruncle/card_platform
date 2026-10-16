from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from card_platform.news_alerts import NewsAlertInput, validate_freshness

NOW = datetime(2026, 9, 28, 2, tzinfo=timezone.utc)


def payload(**changes):
    return dict(player_id=str(uuid4()), event_key='injury-2026-09-27',
                direction='negative', headline='球队公布伤病更新',
                facts='球队确认将缺席两周。', attribution='缺席可能影响短期关注和流动性；尚无成交证据。',
                confidence='medium', source_url='https://www.nba.com/news/example',
                published_at=NOW.isoformat(), **changes)


def test_valid_positive_and_negative():
    for direction in ('negative', 'positive'):
        data = payload()
        data['direction'] = direction
        validate_freshness(NewsAlertInput(**data), NOW)


@pytest.mark.parametrize('field,value', [('source_url', 'javascript:alert(1)'),
    ('source_url', 'file:///etc/passwd'), ('facts', ''), ('attribution', ' '),
    ('direction', 'neutral'), ('event_key', ''), ('published_at', '2026-09-28T02:00:00')])
def test_reject_invalid_evidence(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(ValidationError):
        NewsAlertInput(**data)


@pytest.mark.parametrize('offset', [timedelta(seconds=1), -timedelta(hours=72, seconds=1)])
def test_reject_future_and_old_news(offset):
    data = payload()
    data['published_at'] = NOW + offset
    with pytest.raises(ValueError):
        validate_freshness(NewsAlertInput(**data), NOW)
