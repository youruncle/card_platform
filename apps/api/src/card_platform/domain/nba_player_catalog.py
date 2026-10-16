"""Pinned, offline NBA vocabulary shared by knowledge import and legacy consumers."""
import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'data' / 'nba_player_aliases_v1.json'


@lru_cache(maxsize=1)
def nba_player_manifest() -> dict:
    return json.loads(CATALOG_PATH.read_text())
