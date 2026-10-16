"""Persistent public-image discovery. Similarity grants no valuation authority.

Callers must verify each public URL against its immutable source record before
ingesting bytes. Target query images are never written to this derived cache.
The index has no account data, identity decisions, prices, or review receipts.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import cv2
import numpy as np

from card_platform.image_geometry import geometric_feature_score
from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url


FEATURE_VERSION = f'sift-550-900-physical-v1-opencv-{cv2.__version__}'
MAX_BYTES = 10 * 1024 * 1024


class PublicImageIndex:
    def __init__(self, path, *, read_only=False):
        path = Path(path)
        if path.is_symlink():
            raise ValueError('index_symlink')
        self._loaded = None
        if read_only:
            self.db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                version = self.db.execute("SELECT value FROM metadata WHERE key='feature_version'").fetchone()
                if not version or version[0] != FEATURE_VERSION:
                    raise ValueError('feature_version_changed_rebuild_index')
                self._data_version = self.db.execute('PRAGMA data_version').fetchone()[0]
            except Exception:
                self.db.close()
                raise
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        # Building a query index can hold a read snapshot longer than SQLite's
        # lock timeout. WAL lets ingestion commit without interrupting that
        # reader; existing data_version checks refresh its next query.
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS images(
                sha256 TEXT PRIMARY KEY, height INTEGER NOT NULL, width INTEGER NOT NULL,
                count INTEGER NOT NULL, points BLOB NOT NULL, descriptors BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
                source_url TEXT NOT NULL, image_url TEXT NOT NULL, provider TEXT NOT NULL,
                sha256 TEXT NOT NULL REFERENCES images(sha256), captured_at TEXT NOT NULL,
                PRIMARY KEY(source_url,image_url));
        ''')
        version = self.db.execute("SELECT value FROM metadata WHERE key='feature_version'").fetchone()
        if version and version[0] != FEATURE_VERSION:
            self.db.close()
            raise ValueError('feature_version_changed_rebuild_index')
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES('feature_version',?)", (FEATURE_VERSION,))
        self.db.commit()
        self._loaded = None
        self._data_version = self.db.execute('PRAGMA data_version').fetchone()[0]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @staticmethod
    def _extract(content):
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
            raise ValueError('invalid_image_bytes')
        gray = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.size > 40_000_000:
            raise ValueError('invalid_image_dimensions')
        h, w = gray.shape
        scale = min(1., 900 / max(h, w))
        if scale < 1:
            gray = cv2.resize(gray, (max(1, round(w*scale)), max(1, round(h*scale))))
        points, descriptors = cv2.SIFT_create(nfeatures=550).detectAndCompute(gray, None)
        if descriptors is None or len(descriptors) < 8:
            raise ValueError('insufficient_image_features')
        return (np.array([p.pt for p in points], dtype=np.float32),
                np.asarray(descriptors, dtype=np.float32), gray.shape)

    @staticmethod
    def _validate(record, content):
        source = canonical_public_item_url(record.get('source_url'))
        try:
            url = urlsplit(record.get('image_url', ''))
            captured = datetime.fromisoformat(record['captured_at'])
            public_transform = not url.query or bool(re.fullmatch(
                r'imageView2/\d+(?:/[a-z]+/\d+)*=?', unquote(url.query)))
            valid = (record.get('provider') == 'cardhobby' and source
                     and url.scheme == 'https' and url.netloc == 'images.cardhobby.com'
                     and not (url.username or url.password or url.port or url.fragment)
                     and public_transform
                     and bool(url.path.strip('/')) and captured.tzinfo is not None
                     and captured <= datetime.now(timezone.utc))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid or not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
            raise ValueError('invalid_public_image_record')
        sha = hashlib.sha256(content).hexdigest()
        if sha != record.get('sha256'):
            raise ValueError('image_content_hash_mismatch')
        return source, sha

    def upsert(self, record, content):
        source, sha = self._validate(record, content)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous = self.db.execute('SELECT captured_at FROM sources WHERE source_url=? AND image_url=?',
                (source, record['image_url'])).fetchone()
            if previous and datetime.fromisoformat(previous[0]) > datetime.fromisoformat(record['captured_at']):
                return 'stale'
            exists = self.db.execute('SELECT 1 FROM images WHERE sha256=?', (sha,)).fetchone()
            features = None if exists else self._extract(content)
            if features is not None:
                points, descriptors, shape = features
                self.db.execute('INSERT INTO images VALUES(?,?,?,?,?,?)',
                    (sha, *shape, len(points), points.tobytes(), descriptors.tobytes()))
            self.db.execute('''INSERT INTO sources VALUES(?,?,?,?,?)
                ON CONFLICT(source_url,image_url) DO UPDATE SET
                sha256=excluded.sha256,captured_at=excluded.captured_at''',
                (source, record['image_url'], record['provider'], sha, record['captured_at']))
            self._prune()
        self._loaded = None
        return 'reused' if exists else 'computed'

    def _prune(self):
        self.db.execute('DELETE FROM images WHERE sha256 NOT IN (SELECT sha256 FROM sources)')

    def remove_source(self, source_url):
        canonical = canonical_public_item_url(source_url)
        if not canonical:
            raise ValueError('invalid_public_source')
        with self.db:
            count = self.db.execute('DELETE FROM sources WHERE source_url=?', (canonical,)).rowcount
            self._prune()
        self._loaded = None
        return count

    def stats(self):
        return dict(images=self.db.execute('SELECT count(*) FROM images').fetchone()[0],
                    sources=self.db.execute('SELECT count(*) FROM sources').fetchone()[0],
                    descriptors=self.db.execute('SELECT coalesce(sum(count),0) FROM images').fetchone()[0],
                    feature_version=FEATURE_VERSION)

    def _load(self):
        version = self.db.execute('PRAGMA data_version').fetchone()[0]
        if version != self._data_version:
            self._loaded = None
            feature = self.db.execute("SELECT value FROM metadata WHERE key='feature_version'").fetchone()
            if not feature or feature[0] != FEATURE_VERSION:
                raise ValueError('feature_version_changed_rebuild_index')
            self._data_version = version
        if self._loaded is not None:
            return self._loaded
        assets = []
        for sha, h, w, count, points, descriptors in self.db.execute('SELECT * FROM images ORDER BY sha256'):
            if (count < 8 or count > 5000 or len(points) != count*2*4
                    or len(descriptors) != count*128*4 or not 0 < h <= 900 or not 0 < w <= 900):
                raise ValueError('corrupt_index_features')
            points = np.frombuffer(points, np.float32).reshape(count, 2)
            descriptors = np.frombuffer(descriptors, np.float32).reshape(count, 128)
            if not np.isfinite(points).all() or not np.isfinite(descriptors).all():
                raise ValueError('corrupt_index_features')
            assets.append((sha, points, descriptors, (h, w)))
        if not assets:
            return None
        descriptors = np.concatenate([a[2] for a in assets])
        owners = np.concatenate([np.full(len(a[2]), i, np.int32) for i, a in enumerate(assets)])
        index = cv2.flann_Index(descriptors, dict(algorithm=1, trees=4))
        # Keep the descriptor backing array alive with the OpenCV index.
        self._loaded = assets, owners, index, descriptors
        return self._loaded

    def query(self, content, *, limit=12, shortlist=35):
        if not 1 <= limit <= 100 or not 1 <= shortlist <= 200:
            raise ValueError('invalid_query_budget')
        left = self._extract(content)
        loaded = self._load()
        if loaded is None:
            return []
        assets, owners, index, _ = loaded
        neighbors, _ = index.knnSearch(left[1], 3, params=dict(checks=64))
        votes = Counter()
        for near in neighbors:
            votes.update(set(int(owners[n]) for n in near if 0 <= n < len(owners)))
        rows = []
        for position, vote in votes.most_common(shortlist):
            sha, points, descriptors, shape = assets[position]
            good, inliers, ratio = geometric_feature_score(
                [SimpleNamespace(pt=p) for p in left[0]], left[1], left[2],
                [SimpleNamespace(pt=p) for p in points], descriptors, shape)
            if inliers < 8:
                continue
            for source, image, provider, captured in self.db.execute(
                    'SELECT source_url,image_url,provider,captured_at FROM sources WHERE sha256=? ORDER BY source_url,image_url', (sha,)):
                rows.append(dict(source_url=source, image_url=image, provider=provider,
                    captured_at=captured, sha256=sha, matches=good, inliers=inliers,
                    ratio=float(ratio), votes=vote, discovery_only=True,
                    feature_version=FEATURE_VERSION))
        rows.sort(key=lambda r: (-r['inliers'], -r['ratio'], -r['votes'], r['source_url'], r['image_url']))
        return rows[:limit]
