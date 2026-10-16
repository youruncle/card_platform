"""Local recovery inputs. No network requests run on portfolio reads."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from card_platform.config import get_settings


@lru_cache(maxsize=2048)
def _hash_file(path, size, modified_ns):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_image_hashes(item):
    # Use exactly the directory served by FastAPI; never bind a stale backup.
    directories = [get_settings().resolved_upload_dir]
    hashes = {}
    for side in ("front", "back"):
        url = item.get("image_" + side + "_url") or ""
        if not url.startswith("/uploads/"):
            continue
        name = url.removeprefix("/uploads/")
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            continue
        for directory in directories:
            path = (directory / name).resolve()
            if path.parent != directory.resolve():
                continue
            try:
                stat = path.stat()
                if stat.st_size > 20 * 1024 * 1024:
                    continue
                hashes[side] = _hash_file(str(path), stat.st_size, stat.st_mtime_ns)
                break
            except OSError:
                continue
    return hashes


@lru_cache(maxsize=1)
def recovery_reviews():
    path = Path(__file__).parent / "data/codex_recovered_sales.json"
    return json.loads(path.read_text()) if path.exists() else {"sources": {}, "fx": {}}
