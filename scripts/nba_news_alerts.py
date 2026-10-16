"""Run from repo root: PYTHONPATH=apps/api/src .venv/bin/python scripts/nba_news_alerts.py."""
import argparse
import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.news_alerts import NewsAlertInput, import_alerts, research_context


async def run(args):
    # Validate the complete batch before connecting or writing anything.
    alerts = (TypeAdapter(list[NewsAlertInput]).validate_json(Path(args.file).read_text())
              if args.command == 'import' else None)
    database = Database(get_settings())
    await database.open()
    try:
        async with database.connection() as connection:
            result = (await import_alerts(connection, alerts) if alerts is not None
                      else await research_context(connection))
            print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    finally:
        await database.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='NBA 新闻风险提示研究上下文和幂等导入')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('roster')
    commands.add_parser('import').add_argument('file')
    asyncio.run(run(parser.parse_args()))
