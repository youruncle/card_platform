import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "rerun_unvalued_cards.py"
SPEC = importlib.util.spec_from_file_location("rerun_unvalued_cards_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


def test_refresh_first_is_opt_in_and_requires_explicit_targets() -> None:
    assert SCRIPT.parse_args([]).refresh_first is False

    with pytest.raises(SystemExit, match="--refresh-first requires"):
        SCRIPT.parse_args(["--refresh-first"])

    args = SCRIPT.parse_args(["--refresh-first", "--display-id", "361", "--display-id", "370"])
    assert args.refresh_first is True
    assert args.display_ids == [361, 370]


def test_cached_only_can_follow_one_explicit_cardhobby_refresh() -> None:
    assert SCRIPT.parse_args([]).cached_only is False
    assert SCRIPT.parse_args(["--cached-only"]).cached_only is True

    args = SCRIPT.parse_args(
        ["--cached-only", "--refresh-first", "--display-id", "361"]
    )
    assert args.cached_only is True
    assert args.refresh_first is True
    assert args.display_ids == [361]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--concurrency", "0"], "--concurrency"),
        (["--concurrency", "9"], "--concurrency"),
        (["--timeout", "0"], "--timeout"),
        (["--limit", "0"], "--limit"),
        (["--include-valued"], "--include-valued requires"),
    ],
)
def test_unsafe_batch_parameters_fail_before_database_access(
    arguments: list[str], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        SCRIPT.parse_args(arguments)
