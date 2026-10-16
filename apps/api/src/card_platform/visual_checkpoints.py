"""Private, atomic recovery files; no state is exposed through /uploads/."""
import base64
import binascii
import json
import os
from pathlib import Path
import tempfile
import time

from card_platform.valuation_trace import content_hash


class VisualCheckpoints:
    IMAGE_TTL_SECONDS = 24 * 60 * 60
    RETRY_WINDOW_SECONDS = 60 * 60
    MAX_ATTEMPTS = 3

    def __init__(self, upload_dir: Path):
        self.root = upload_dir.parent / '.visual-checkpoints' / content_hash(str(upload_dir))[:16]

    def read(self, kind: str, key: str, *, max_record_bytes: int = 12 * 1024 * 1024) -> dict:
        try:
            path = self.root / kind / f'{key}.json'
            if path.stat().st_size > max_record_bytes:
                return {}
            record = json.loads(path.read_text())
            return record if isinstance(record, dict) else {}
        except (OSError, ValueError):
            return {}

    def write(self, kind: str, key: str, record: dict) -> None:
        temporary = None
        try:
            directory = self.root / kind
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as stream:
                temporary = stream.name
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / f'{key}.json')
        except OSError:
            # A full/unwritable local cache must not erase a live observation.
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def image(self, key: str, max_bytes: int) -> tuple[bytes, str] | None:
        # Base64 expands an accepted original; leave bounded room for metadata.
        max_encoded_bytes = 4 * ((max_bytes + 2) // 3)
        record = self.read('images', key, max_record_bytes=max_encoded_bytes + 1024)
        try:
            age = time.time() - float(record['saved_at'])
            if not 0 <= age < self.IMAGE_TTL_SECONDS:
                return None
            if len(record['content']) > max_encoded_bytes:
                return None
            content = base64.b64decode(record['content'], validate=True)
            if (not content or len(content) > max_bytes
                    or record['sha256'] != content_hash(content)
                    or record['content_type'] not in {'image/jpeg', 'image/png', 'image/webp'}):
                return None
            return content, record['content_type']
        except (KeyError, ValueError, TypeError, binascii.Error):
            return None

    def save_image(self, key: str, content: bytes, content_type: str) -> None:
        self.write('images', key, {'saved_at': time.time(), 'sha256': content_hash(content),
                                  'content_type': content_type,
                                  'content': base64.b64encode(content).decode('ascii')})

    def retry_state(self, record: dict) -> tuple[int, float, bool]:
        try:
            attempts = int(record.get('attempts', 0))
            started = float(record.get('window_started', time.time()))
            last = float(record.get('last_attempt', 0))
            age = time.time() - started
            if attempts < 0 or not 0 <= age < self.RETRY_WINDOW_SECONDS:
                return 0, time.time(), False
            deferred = attempts >= self.MAX_ATTEMPTS or time.time() - last < min(30 * 2 ** min(attempts, 5), 900)
            return attempts, started, bool(attempts and deferred)
        except (ValueError, TypeError, OverflowError):
            return 0, time.time(), False

    def reserve(self, key: str, record: dict) -> dict:
        attempts, started, _ = self.retry_state(record)
        state = {'attempts': attempts + 1, 'window_started': started, 'last_attempt': time.time()}
        # An interrupted attempt also consumes its bounded retry window.
        self.write('decisions', key, state)
        return state
