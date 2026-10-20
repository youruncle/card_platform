# Validation quickstart

Run from apps/api with the repository Python 3.12 virtualenv:

```sh
python -m pytest tests/test_deepseek_web.py tests/test_deepseek_vision.py tests/test_card_search_service.py tests/test_card_search_concurrency.py tests/test_cardhobby_provider.py tests/test_search_index.py -q
```

Use private release tooling for real calls with server-side settings. Check actual /v1/tools/search and /v1/tools/fetch responses, real grounding context and thinking.type=disabled, then inspect unsupported/fabricated-source rejection. Do not use real collection writes as a smoke test.

Before activation compare current running image IDs and full source manifest to the captured baseline. After activation assert API ready, worker ENV and Settings true, visual/search models deepseek-flash on official endpoints; check homepage/player-prices/capabilities/static resources, private anonymous access, then installed iPhone version and running process. Record partial Fetch failures and untested business UI paths.
