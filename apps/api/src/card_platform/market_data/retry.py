"""Bounded durable retry policy for transient market-source failures."""
import random
import re

MAX_REFRESH_ATTEMPTS = 4  # initial attempt plus at most three retries
TRANSIENT_ERROR = re.compile(
    r"timeout|timed?\s*out|超时|connecterror|networkerror|connection reset|"
    r"HTTP\s*(?:429|502|503|504)\b|circuit open|concurrency wait", re.I,
)


def retry_delay_seconds(error: str | None, attempt_count: int) -> int | None:
    if not error or not TRANSIENT_ERROR.search(error):
        return None
    if attempt_count < 1 or attempt_count >= MAX_REFRESH_ATTEMPTS:
        return None
    return random.randint(5, 15) if attempt_count == 1 else random.randint(180, 300)
