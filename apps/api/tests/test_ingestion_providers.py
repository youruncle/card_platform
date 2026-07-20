from card_platform.ingestion.providers import NBAStatsProvider, RSSProvider


def test_nba_stats_payload_is_normalized() -> None:
    payload = {
        "resultSets": [
            {
                "name": "SeasonTotalsRegularSeason",
                "headers": [
                    "SEASON_ID",
                    "TEAM_ID",
                    "TEAM_ABBREVIATION",
                    "GP",
                    "MIN",
                    "PTS",
                    "REB",
                    "AST",
                    "STL",
                    "BLK",
                    "FG_PCT",
                    "FG3_PCT",
                    "FT_PCT",
                ],
                "rowSet": [
                    [
                        "2025-26",
                        1610612765,
                        "DET",
                        80,
                        30.5,
                        15.2,
                        10.1,
                        2.4,
                        0.8,
                        1.1,
                        0.61,
                        0.22,
                        0.79,
                    ]
                ],
            }
        ]
    }

    records = NBAStatsProvider.parse(payload, 1631105)

    assert len(records) == 1
    assert records[0].season == "2025-26"
    assert records[0].metrics["reb"] == 10.1
    assert records[0].external_id == "1631105:2025-26:1610612765"


def test_rss_items_are_normalized_and_stably_identified() -> None:
    xml = """<?xml version="1.0"?><rss><channel><item>
      <title>Player returns to practice</title>
      <link>https://example.com/story/1</link>
      <description>Expected to play Friday.</description>
      <pubDate>Fri, 17 Jul 2026 10:00:00 GMT</pubDate>
    </item></channel></rss>"""
    provider = RSSProvider("injury_rss", "https://example.com?q={player}", "injury")

    first = provider.parse(xml)[0]
    second = provider.parse(xml)[0]

    assert first.event_type == "injury"
    assert first.headline == "Player returns to practice"
    assert first.external_id == second.external_id
