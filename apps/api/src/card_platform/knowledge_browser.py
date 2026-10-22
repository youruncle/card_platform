"""Rebuildable, read-only SQLite view of one published knowledge snapshot.

Only the derived cache is written. Attachments are validated one at a time and
never expanded into a process-wide card collection. Each read owns its SQLite
connection so API worker threads cannot share thread-bound connections.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import fcntl
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unicodedata
import zlib
from urllib.parse import quote, urlencode, urlsplit

_SCHEMA = 4
_ROOT = Path(__file__).resolve().parents[4]
_MAKER_BRANDS = {
    "topps": "Topps",
    "panini": "Panini",
    "upperdeck": "Upper Deck",
    "fleer": "Fleer",
    "skybox": "SkyBox",
    "leaf": "Leaf",
}
_DESIGN_TYPES = {
    "official_design",
    "official_product_image",
    "official_product_design",
    "manufacturer_design",
    "design_reference",
    "official_design_sample",
    "product_design",
    "source_design",
    "source_design_reference",
}
_SPECIMEN_TYPES = {
    "verified_card_scan",
    "verified_card",
    "verified_specimen",
    "card_scan",
    "reviewed_card_scan",
    "specimen",
}


def _key(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", str(value or "").casefold())
        if c.isalnum() and not unicodedata.combining(c)
    )


def _text(value):
    return (
        str(value).strip() if isinstance(value, (str, int, float)) and str(value).strip() else None
    )


def _year(value):
    return value if type(value) is int else None


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        return (
            value
            if parts.scheme in {"http", "https"}
            and parts.netloc
            and not parts.username
            and not parts.password
            else None
        )
    except ValueError:
        return None


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _approved(row):
    return (
        isinstance(row, dict)
        and row.get("active", True) is not False
        and row.get("review_status", "approved") == "approved"
    )


def _attrs(row):
    return row.get("attributes") if isinstance(row.get("attributes"), dict) else {}


def _aliases(row):
    aliases = row.get("aliases")
    return (
        list(dict.fromkeys(v for v in aliases if isinstance(v, str)))
        if isinstance(aliases, list)
        else []
    )


def _safe_path(root, value):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ValueError("reference path is outside the allowed root")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError("reference path is outside the allowed root")
    return path


class KnowledgeBrowser:
    def __init__(
        self,
        snapshot: dict,
        *,
        version_id: str | None = None,
        artifact_root: Path | None = None,
        data_root: Path | None = None,
        cache_root: Path | None = None,
    ):
        self.snapshot = snapshot
        self.version = snapshot.get("version") if type(snapshot.get("version")) is int else None
        self.version_id = str(version_id) if version_id is not None else None
        self.artifact_root = Path(
            artifact_root or _ROOT / "data/official-checklists/artifacts"
        ).resolve()
        self.data_root = Path(data_root or _ROOT / "data").resolve()
        self.cache_root = Path(cache_root or _ROOT / ".runtime/knowledge-browser").resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._refs = []
        seen = set()
        for ref in snapshot.get("official_artifacts", []):
            if isinstance(ref, dict):
                signature = _json(ref)
                if signature not in seen:
                    seen.add(signature)
                    self._refs.append(ref)
        for row in snapshot.get("entities", []):
            ref = _attrs(row).get("official_artifact")
            if _approved(row) and isinstance(ref, dict):
                signature = _json(ref)
                if signature not in seen:
                    seen.add(signature)
                    self._refs.append(ref)
        digest = sha256()
        for piece in json.JSONEncoder(sort_keys=True, ensure_ascii=False, default=str).iterencode(
            snapshot
        ):
            digest.update(piece.encode())
        digest.update(
            _json([_SCHEMA, self.version_id, str(self.data_root), str(self.artifact_root)]).encode()
        )
        self._snapshot_key = digest.hexdigest()
        self._state = None
        self._ensure_index()

    def _source_state(self):
        state = []
        for ref in self._refs:
            try:
                stat = _safe_path(self.artifact_root, ref.get("path")).stat()
                state.append(
                    (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                )
            except (OSError, ValueError):
                state.append(None)
        return state

    def _ensure_index(self):
        state = self._source_state()
        with self._lock:
            if state == self._state:
                return
            key = sha256((self._snapshot_key + _json(state)).encode()).hexdigest()
            target = self.cache_root / f"{key}.sqlite3"
            with (self.cache_root / f"{key}.lock").open("a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if not self._valid_cache(target):
                    fd, name = tempfile.mkstemp(
                        prefix=f"{key}.", suffix=".tmp", dir=self.cache_root
                    )
                    os.close(fd)
                    temp = Path(name)
                    try:
                        self._build(temp)
                        os.replace(temp, target)
                    finally:
                        temp.unlink(missing_ok=True)
                fcntl.flock(lock, fcntl.LOCK_UN)
            self.cache_path = target
            self._state = state

    @staticmethod
    def _valid_cache(path):
        if not path.is_file():
            return False
        try:
            with closing(sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)) as db:
                return db.execute("SELECT value FROM meta WHERE key='complete'").fetchone() == (
                    str(_SCHEMA),
                )
        except sqlite3.Error:
            return False

    def _connect(self):
        self._ensure_index()
        db = sqlite3.connect(f"file:{quote(str(self.cache_path))}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def _build(self, path):
        issues = []
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE products(id TEXT PRIMARY KEY, name TEXT, manufacturer TEXT, season INTEGER,
                  search TEXT, data TEXT);
                CREATE TABLE cards(id TEXT PRIMARY KEY, product_id TEXT, manufacturer TEXT, season INTEGER,
                  season_end INTEGER, player TEXT, card_number TEXT, section TEXT, parallel TEXT,
                  image_count INTEGER DEFAULT 0, front TEXT, back TEXT, data TEXT);
                CREATE TABLE card_search(card_rowid INTEGER PRIMARY KEY, search TEXT);
                CREATE TABLE media(id TEXT PRIMARY KEY, product_id TEXT, card_id TEXT, kind TEXT,
                  side TEXT, pair_id TEXT, path TEXT, sha256 TEXT, data TEXT);
                CREATE TABLE card_media(card_id TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE entities(id TEXT PRIMARY KEY, entity_type TEXT, search TEXT, data TEXT);
                CREATE TABLE manufacturers(name TEXT PRIMARY KEY);
            """)
            # Only product/brand/manufacturer metadata is held beside the supplied
            # snapshot; the hundreds of thousands of cards stay in SQLite.
            entity_map = {
                str(e.get("id")): e
                for e in self.snapshot.get("entities", [])
                if _approved(e) and e.get("entity_type") in {"product", "brand", "manufacturer"}
            }
            ancestry = defaultdict(list)
            for rel in self.snapshot.get("relations", []):
                if _approved(rel) and rel.get("relation_type") in {
                    "belongs_to",
                    "manufactured_by",
                    "product_family_of",
                }:
                    ancestry[str(rel.get("subject_entity_id"))].append(
                        str(rel.get("object_entity_id"))
                    )

            def hierarchy(entity, kind):
                pending, visited = [str(entity.get("id"))], set()
                while pending:
                    eid = pending.pop()
                    if eid in visited:
                        continue
                    visited.add(eid)
                    current = entity_map.get(eid, {})
                    if current.get("entity_type") == kind:
                        return _text(current.get("canonical_name"))
                    attrs = _attrs(current)
                    if kind == "manufacturer" and _text(attrs.get("manufacturer")):
                        return _text(attrs["manufacturer"])
                    if current.get("parent_id"):
                        pending.append(str(current["parent_id"]))
                    pending.extend(ancestry.get(eid, []))
                return None

            products, product_names = {}, defaultdict(list)
            for entity in entity_map.values():
                if entity.get("entity_type") == "manufacturer":
                    db.execute(
                        "INSERT OR IGNORE INTO manufacturers VALUES(?)",
                        (_text(entity.get("canonical_name")),),
                    )
                if entity.get("entity_type") != "product":
                    continue
                attrs = _attrs(entity)
                brand = _text(attrs.get("brand")) or hierarchy(entity, "brand")
                manufacturer = (
                    _text(attrs.get("manufacturer"))
                    or hierarchy(entity, "manufacturer")
                    or _MAKER_BRANDS.get(_key(brand))
                )
                product = {
                    "id": str(entity["id"]),
                    "name": _text(entity.get("canonical_name")) or "产品名称待补",
                    "manufacturer": manufacturer,
                    "brand": brand,
                    "season_start": _year(attrs.get("season_start")),
                    "season_end": _year(attrs.get("season_end")),
                    "sport": _text(attrs.get("sport")),
                    "league": _text(attrs.get("league")),
                    "aliases": _aliases(entity),
                    "description": _text(attrs.get("description")),
                    "card_count": 0,
                    "image_count": 0,
                    "paired_card_count": 0,
                    "text_status": "missing",
                    "source_url": _url(attrs.get("source_url")),
                    "source_authority": _text(
                        attrs.get("source_authority") or attrs.get("authority")
                    ),
                    "cover_image_url": None,
                }
                products[product["id"]] = product
                names = {
                    _key(product["name"]),
                    _key(attrs.get("product")),
                    *(_key(a) for a in product["aliases"]),
                } - {""}
                for name in names:
                    product_names[(name, product["season_start"], product["season_end"])].append(
                        product["id"]
                    )
                if manufacturer:
                    db.execute("INSERT OR IGNORE INTO manufacturers VALUES(?)", (manufacturer,))

            # Reviewed generic vocabulary can enrich the exact annual family.
            # Strip only its explicitly recorded maker prefix, never arbitrary
            # suffixes such as Optic, Collegiate, Sapphire, or Updates.
            def family_label(entity, product):
                label = _text(_attrs(entity).get("product")) or product["name"]
                maker = product["manufacturer"]
                if maker and label.casefold().startswith(maker.casefold() + " "):
                    label = label[len(maker) :].strip()
                return _key(label)

            generic = defaultdict(list)
            for pid, product in products.items():
                if product["season_start"] is None:
                    generic[family_label(entity_map[pid], product)].append(product)
            for pid, product in products.items():
                if product["season_start"] is None:
                    continue
                for base in generic.get(family_label(entity_map[pid], product), []):
                    if base["manufacturer"] and _key(base["manufacturer"]) != _key(
                        product["manufacturer"]
                    ):
                        continue
                    if base["brand"] and _key(base["brand"]) != _key(product["brand"]):
                        continue
                    product["aliases"] = list(
                        dict.fromkeys([*product["aliases"], *base["aliases"]])
                    )
            player_aliases = defaultdict(list)
            for entity in self.snapshot.get("entities", []):
                if _approved(entity) and entity.get("entity_type") == "player":
                    player_aliases[_key(entity.get("canonical_name"))].extend(_aliases(entity))

            def bind(attrs):
                explicit = attrs.get("product_id") or attrs.get("product_entity_id")
                possible = (
                    [str(explicit)]
                    if explicit is not None
                    else product_names.get(
                        (
                            _key(attrs.get("product")),
                            _year(attrs.get("season_start")),
                            _year(attrs.get("season_end")),
                        ),
                        [],
                    )
                )
                matched = []
                for pid in possible:
                    p = products.get(pid)
                    if not p:
                        continue
                    if any(
                        _key(attrs.get(field))
                        and _key(p.get(field))
                        and _key(attrs[field]) != _key(p[field])
                        for field in ("brand", "manufacturer")
                    ):
                        continue
                    if any(
                        attrs.get(field) != p[field] for field in ("season_start", "season_end")
                    ):
                        continue
                    matched.append(pid)
                matched = list(dict.fromkeys(matched))
                if len(matched) > 1:
                    # Duplicate catalogue releases require published source
                    # evidence; ordering is never an identity tie-breaker.
                    for field in ("source_key", "source_url"):
                        observed = attrs.get(field)
                        if observed:
                            scoped = [
                                pid
                                for pid in matched
                                if observed
                                in {
                                    _attrs(entity_map[pid]).get(field),
                                    (_attrs(entity_map[pid]).get("official_artifact") or {}).get(
                                        field
                                    ),
                                }
                            ]
                            if scoped:
                                matched = scoped
                return matched[0] if len(matched) == 1 else None

            def add_card(entity, inherited=None):
                attrs = _attrs(entity)
                pid = bind(attrs)
                if inherited and pid is None:
                    pid = bind({**inherited, **attrs})
                p = products.get(pid, {})
                sources = []
                source_url = _url(attrs.get("source_url"))
                explicit_locator = _text(attrs.get("source_locator"))
                locators = (
                    [explicit_locator]
                    if explicit_locator
                    else attrs.get("source_locators") or [None]
                )
                if not isinstance(locators, list):
                    locators = [locators]
                if source_url:
                    for locator in locators[:30]:
                        if isinstance(locator, dict):
                            page = _text(locator.get("source_page"))
                            line = _text(locator.get("source_line"))
                            locator = (
                                ", ".join(
                                    v
                                    for v in [
                                        f"page {page}" if page else None,
                                        f"line {line}" if line else None,
                                    ]
                                    if v
                                )
                                or None
                            )
                        locator = _text(locator)
                        # Locator prose is public evidence; filesystem paths are not.
                        if locator and (
                            locator.startswith(("/", "file:", "../"))
                            or re.match(r"^[A-Za-z]:[\\/]", locator)
                        ):
                            locator = None
                        sources.append(
                            {
                                "url": source_url,
                                "locator": locator,
                                "authority": _text(
                                    attrs.get("source_authority") or attrs.get("authority")
                                ),
                            }
                        )
                manufacturer = (
                    _text(attrs.get("manufacturer"))
                    or p.get("manufacturer")
                    or _MAKER_BRANDS.get(_key(attrs.get("brand")))
                )
                row = {
                    "id": str(entity["id"]),
                    "name": _text(entity.get("canonical_name")) or "卡牌名称待补",
                    "product_id": pid,
                    "product_name": p.get("name") or _text(attrs.get("product")),
                    "manufacturer": manufacturer,
                    "season_start": _year(attrs.get("season_start")),
                    "season_end": _year(attrs.get("season_end")),
                    "player": _text(attrs.get("player")),
                    "card_number": _text(attrs.get("card_number")),
                    "section": _text(attrs.get("section")),
                    "parallel": _text(attrs.get("parallel")),
                    "serial_total": attrs.get("serial_total")
                    if isinstance(attrs.get("serial_total"), (int, str))
                    else None,
                    "is_rookie": attrs.get("is_rookie")
                    if type(attrs.get("is_rookie")) is bool
                    else None,
                    "is_autograph": attrs.get("is_autograph")
                    if type(attrs.get("is_autograph")) is bool
                    else None,
                    "is_patch": attrs.get("is_patch")
                    if type(attrs.get("is_patch")) is bool
                    else None,
                    "image_count": 0,
                    "front_image_url": None,
                    "back_image_url": None,
                    "sources": sources,
                }
                search = " ".join(
                    _key(v)
                    for v in [
                        row["name"],
                        row["player"],
                        row["card_number"],
                        row["product_name"],
                        row["section"],
                        row["parallel"],
                        *_aliases(entity),
                        *p.get("aliases", []),
                        *player_aliases.get(_key(row["player"]), []),
                    ]
                    if v
                )
                inserted = db.execute(
                    "INSERT OR IGNORE INTO cards(id,product_id,manufacturer,season,season_end,player,card_number,section,parallel,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        pid,
                        manufacturer,
                        row["season_start"],
                        row["season_end"],
                        _key(row["player"]),
                        _key(row["card_number"]),
                        row["section"],
                        row["parallel"],
                        zlib.compress(_json(row).encode(), level=1),
                    ),
                )
                if inserted.rowcount:
                    db.execute("INSERT INTO card_search VALUES(?,?)", (inserted.lastrowid, search))
                refs = attrs.get("reference_images")
                if isinstance(refs, list) and refs:
                    db.execute(
                        "INSERT OR IGNORE INTO card_media VALUES(?,?)", (row["id"], _json(refs))
                    )
                if manufacturer:
                    db.execute("INSERT OR IGNORE INTO manufacturers VALUES(?)", (manufacturer,))

            for entity in self.snapshot.get("entities", []):
                if not _approved(entity) or not entity.get("id"):
                    continue
                attrs = _attrs(entity)
                entry = {
                    "id": str(entity["id"]),
                    "entity_type": str(entity.get("entity_type") or "unknown"),
                    "canonical_name": _text(entity.get("canonical_name")) or "Unknown",
                    "aliases": _aliases(entity),
                    "description": _text(attrs.get("description")),
                    "source_url": _url(attrs.get("source_url")),
                }
                db.execute(
                    "INSERT OR IGNORE INTO entities VALUES(?,?,?,?)",
                    (
                        entry["id"],
                        entry["entity_type"],
                        " ".join(_key(v) for v in [entry["canonical_name"], *entry["aliases"]]),
                        _json(entry),
                    ),
                )
                if entity.get("entity_type") == "card":
                    add_card(entity)
            for ref in self._refs:
                source_key = _text(ref.get("source_key")) or "unknown"
                try:
                    artifact = _safe_path(self.artifact_root, ref.get("path"))
                    raw = artifact.read_bytes()
                    if sha256(raw).hexdigest() != ref.get("checksum"):
                        raise ValueError("attachment checksum mismatch")
                    cards = json.loads(gzip.decompress(raw))
                    del raw
                    if (
                        not isinstance(cards, list)
                        or type(ref.get("card_count")) is not int
                        or len(cards) != ref["card_count"]
                    ):
                        raise ValueError("attachment card count or shape mismatch")
                    # Validate the entire attachment before admitting any of it.
                    for card in cards:
                        if (
                            not isinstance(card, dict)
                            or not isinstance(card.get("id"), str)
                            or card.get("entity_type") != "card"
                            or not isinstance(card.get("attributes"), dict)
                        ):
                            raise ValueError("attachment contains an invalid card row")
                        attrs = card["attributes"]
                        for field in (
                            "source_key",
                            "product",
                            "brand",
                            "manufacturer",
                            "season_start",
                            "season_end",
                        ):
                            if ref.get(field) is not None and attrs.get(field) is not None:
                                expected, actual = ref[field], attrs[field]
                                if (
                                    _key(expected) != _key(actual)
                                    if isinstance(expected, str)
                                    else expected != actual
                                ):
                                    raise ValueError(f"attachment {field} scope mismatch")
                        for field in ("product", "season_start", "season_end"):
                            if ref.get(field) is not None and attrs.get(field) is None:
                                raise ValueError(f"attachment missing {field} scope")
                    for card in cards:
                        add_card(card, ref)
                    del cards
                except (OSError, ValueError, TypeError, EOFError, zlib.error):
                    issues.append(
                        {
                            "source_key": source_key,
                            "message": "已发布清单附件缺失，或未通过哈希、结构、条数及作用域校验。",
                        }
                    )
            db.executescript("""
                CREATE INDEX cards_product ON cards(product_id);
                CREATE INDEX cards_manufacturer_season ON cards(manufacturer COLLATE NOCASE,season);
                CREATE INDEX cards_related ON cards(product_id,season,season_end,player,card_number,section);
                CREATE INDEX entities_type ON entities(entity_type);
            """)
            self._add_media(db, products, entity_map, issues)
            for pid, product in products.items():
                product["card_count"] = db.execute(
                    "SELECT count(*) FROM cards WHERE product_id=?", (pid,)
                ).fetchone()[0]
                product["image_count"] = db.execute(
                    "SELECT count(*) FROM media WHERE product_id=?", (pid,)
                ).fetchone()[0]
                product["paired_card_count"] = self._paired_count(db, "m.product_id=?", [pid])
                cover = db.execute(
                    "SELECT data FROM media WHERE product_id=? ORDER BY CASE side WHEN 'front' THEN 0 ELSE 1 END,id LIMIT 1",
                    (pid,),
                ).fetchone()
                if cover:
                    product["cover_image_url"] = json.loads(cover[0])["thumbnail_url"]
                evidence = _attrs(entity_map[pid]).get("text_evidence") or {}
                if not isinstance(evidence, dict):
                    evidence = {}
                product["text_status"] = (
                    "available"
                    if evidence.get("complete_for_captured_source") and product["card_count"]
                    else "partial"
                    if product["card_count"]
                    else "missing"
                )
                db.execute(
                    "INSERT INTO products VALUES(?,?,?,?,?,?)",
                    (
                        pid,
                        product["name"],
                        product["manufacturer"],
                        product["season_start"],
                        " ".join(_key(v) for v in [product["name"], *product["aliases"]]),
                        _json(product),
                    ),
                )
            db.execute("INSERT INTO meta VALUES('issues',?)", (_json(issues),))
            db.execute("INSERT INTO meta VALUES('complete',?)", (str(_SCHEMA),))
            db.commit()

    def _add_media(self, db, products, entity_map, issues):
        def references():
            for pid in products:
                refs = _attrs(entity_map[pid]).get("reference_images")
                if isinstance(refs, list):
                    yield pid, None, refs
            for card in db.execute(
                "SELECT c.product_id,c.id,m.data FROM card_media m JOIN cards c ON c.id=m.card_id"
            ):
                yield card[0], card[1], json.loads(card[2])

        for pid, owner_card_id, refs in references():
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                image_type = _text(ref.get("image_type"))
                if image_type not in _DESIGN_TYPES | _SPECIMEN_TYPES:
                    continue
                if (
                    ref.get("visibility", "public_reference") != "public_reference"
                    or ref.get("review_status", "approved")
                    not in {"approved", "cross_checked_visual_reference"}
                    or ref.get("status", "ready") != "ready"
                ):
                    continue
                if not re.fullmatch(r"[a-fA-F0-9]{64}", str(ref.get("sha256") or "")):
                    continue
                try:
                    path = _safe_path(self.data_root, ref.get("asset_path"))
                except ValueError:
                    issues.append(
                        {
                            "source_key": _text(ref.get("source_key")) or "media",
                            "message": "已发布图片的本地文件引用无效。",
                        }
                    )
                    continue
                try:
                    if path.stat().st_size > 40 * 1024 * 1024:
                        raise ValueError("image size limit")
                    digest = sha256()
                    with path.open("rb") as image_file:
                        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != ref["sha256"].lower():
                        raise ValueError("image checksum mismatch")
                except (OSError, ValueError):
                    issues.append(
                        {
                            "source_key": _text(ref.get("source_key")) or "media",
                            "message": "已发布图片文件缺失或未通过校验。",
                        }
                    )
                    continue
                kind = "design" if image_type in _DESIGN_TYPES else "specimen"
                card_id = None
                proof = ref.get("identity_verification") or {}
                if not isinstance(proof, dict):
                    proof = {}
                bindings = {
                    str(v)
                    for v in (
                        ref.get("card_id"),
                        owner_card_id,
                        proof.get("card_id") if proof.get("status") == "verified" else None,
                    )
                    if v
                }
                explicit = next(iter(bindings)) if len(bindings) == 1 else None
                if kind == "specimen" and explicit:
                    found = db.execute(
                        "SELECT product_id FROM cards WHERE id=?", (str(explicit),)
                    ).fetchone()
                    if found and found[0] == pid:
                        card_id = str(explicit)
                side = ref.get("side") if ref.get("side") in {"front", "back"} else "unknown"
                media_id = (
                    "media-"
                    + sha256(
                        _json(
                            [
                                pid,
                                ref.get("asset_id"),
                                ref.get("asset_path"),
                                ref["sha256"],
                                card_id,
                                side,
                            ]
                        ).encode()
                    ).hexdigest()[:32]
                )
                image_url = f"/v1/knowledge/browse/media/{quote(media_id, safe='')}/image"
                params = {"version_id": self.version_id} if self.version_id else {}
                row = {
                    "id": media_id,
                    "product_id": pid,
                    "card_id": card_id,
                    "image_url": image_url + ("?" + urlencode(params) if params else ""),
                    "thumbnail_url": image_url + "?" + urlencode({**params, "thumbnail": "true"}),
                    "side": side,
                    "image_type": image_type,
                    "source_url": _url(ref.get("source_url")),
                    "source_authority": _text(ref.get("source_authority") or ref.get("authority")),
                    "caption": _text(ref.get("caption")),
                    "player": _text(ref.get("player")) if card_id else None,
                    "card_number": _text(ref.get("card_number")) if card_id else None,
                    "parallel": _text(ref.get("parallel") or ref.get("variant"))
                    if card_id
                    else None,
                    "pair_id": _text(ref.get("pair_id")) if card_id and side != "unknown" else None,
                }
                db.execute(
                    "INSERT OR IGNORE INTO media VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        media_id,
                        pid,
                        card_id,
                        kind,
                        side,
                        row["pair_id"],
                        str(path),
                        ref["sha256"].lower(),
                        _json(row),
                    ),
                )
        db.executescript(
            "CREATE INDEX media_product ON media(product_id); CREATE INDEX media_card ON media(card_id);"
        )
        # A pair label is meaningful only when its own actual images prove both
        # sides for one exact card and distinct bytes.
        valid_pairs = {
            (r[0], r[1])
            for r in db.execute(
                "SELECT card_id,pair_id FROM media WHERE card_id IS NOT NULL AND pair_id IS NOT NULL GROUP BY card_id,pair_id HAVING count(*)=2 AND count(DISTINCT side)=2 AND count(DISTINCT sha256)=2 AND min(side)='back' AND max(side)='front'"
            )
        }
        for item in db.execute(
            "SELECT id,card_id,pair_id,data FROM media WHERE pair_id IS NOT NULL"
        ).fetchall():
            if (item[1], item[2]) not in valid_pairs:
                row = json.loads(item[3])
                row["pair_id"] = None
                db.execute("UPDATE media SET pair_id=NULL,data=? WHERE id=?", (_json(row), item[0]))
        for item in db.execute(
            "SELECT card_id,count(*) FROM media WHERE card_id IS NOT NULL GROUP BY card_id"
        ).fetchall():
            card_id, count = item
            front = db.execute(
                "SELECT data FROM media WHERE card_id=? AND side='front' ORDER BY id LIMIT 1",
                (card_id,),
            ).fetchone()
            back = db.execute(
                "SELECT data FROM media WHERE card_id=? AND side='back' ORDER BY id LIMIT 1",
                (card_id,),
            ).fetchone()
            db.execute(
                "UPDATE cards SET image_count=?,front=?,back=? WHERE id=?",
                (
                    count,
                    json.loads(front[0])["image_url"] if front else None,
                    json.loads(back[0])["image_url"] if back else None,
                    card_id,
                ),
            )

    @staticmethod
    def _paired_count(db, where="1=1", params=()):
        return db.execute(
            f"SELECT count(DISTINCT m.card_id) FROM media m WHERE m.pair_id IS NOT NULL AND {where}",
            params,
        ).fetchone()[0]

    @staticmethod
    def _bounds(offset, limit):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("offset must be >=0 and limit must be 1..100")

    def _card_row(self, row):
        data = json.loads(zlib.decompress(row["data"]))
        data.update(
            image_count=row["image_count"], front_image_url=row["front"], back_image_url=row["back"]
        )
        return data

    @staticmethod
    def _media_row(db, payload):
        data = json.loads(payload)
        product = db.execute(
            "SELECT data FROM products WHERE id=?", (data.get("product_id"),)
        ).fetchone()
        context = json.loads(product[0]) if product else {}
        data.update(
            product_name=context.get("name"),
            manufacturer=context.get("manufacturer"),
            season_start=context.get("season_start"),
            season_end=context.get("season_end"),
        )
        return data

    def _page(self, db, table, where, params, offset, limit, order="id"):
        self._bounds(offset, limit)
        clause = " AND ".join(where) or "1=1"
        total = db.execute(f"SELECT count(*) FROM {table} WHERE {clause}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM {table} WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {
            "items": [
                self._card_row(r)
                if table == "cards"
                else self._media_row(db, r["data"])
                if table == "media"
                else json.loads(r["data"])
                for r in rows
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
            "version": self.version,
        }

    def summary(self):
        with closing(self._connect()) as db:
            makers = []
            for row in db.execute(
                "SELECT name FROM manufacturers WHERE name IS NOT NULL ORDER BY name COLLATE NOCASE"
            ):
                name = row[0]
                makers.append(
                    {
                        "name": name,
                        "product_count": db.execute(
                            "SELECT count(*) FROM products WHERE manufacturer=?", (name,)
                        ).fetchone()[0],
                        "card_count": db.execute(
                            "SELECT count(*) FROM cards WHERE manufacturer=?", (name,)
                        ).fetchone()[0],
                        "image_count": db.execute(
                            "SELECT count(*) FROM media m LEFT JOIN products p ON p.id=m.product_id "
                            "LEFT JOIN cards c ON c.id=m.card_id WHERE coalesce(p.manufacturer,c.manufacturer)=?",
                            (name,),
                        ).fetchone()[0],
                    }
                )
            return {
                "version": self.version,
                "version_id": self.version_id,
                "discovery_complete": False,
                "summary": {
                    "manufacturers": len(makers),
                    "products": db.execute("SELECT count(*) FROM products").fetchone()[0],
                    "card_facts": db.execute("SELECT count(*) FROM cards").fetchone()[0],
                    "images": db.execute("SELECT count(*) FROM media").fetchone()[0],
                    "paired_cards": self._paired_count(db),
                },
                "manufacturers": makers,
                "seasons": [
                    r[0]
                    for r in db.execute(
                        "SELECT season FROM products WHERE season IS NOT NULL UNION SELECT season FROM cards WHERE season IS NOT NULL ORDER BY season DESC"
                    )
                ],
                "entity_types": [
                    {"type": r[0], "count": r[1]}
                    for r in db.execute(
                        "SELECT entity_type,count(*) FROM entities WHERE entity_type<>'card' GROUP BY entity_type "
                        "UNION ALL SELECT 'card',count(*) FROM cards HAVING count(*)>0 ORDER BY 1"
                    )
                ],
                "issues": json.loads(
                    db.execute("SELECT value FROM meta WHERE key='issues'").fetchone()[0]
                ),
            }

    def products(self, manufacturer=None, season=None, q=None, has_images=None, offset=0, limit=24):
        where, params = [], []
        if manufacturer is not None:
            where.append("lower(manufacturer)=lower(?)")
            params.append(manufacturer)
        if season is not None:
            where.append("season=?")
            params.append(season)
        if q and _key(q):
            where.append("instr(search,?)>0")
            params.append(_key(q))
        if has_images is not None:
            where.append(
                "id "
                + ("IN" if has_images else "NOT IN")
                + " (SELECT product_id FROM media WHERE product_id IS NOT NULL)"
            )
        with closing(self._connect()) as db:
            return self._page(
                db, "products", where, params, offset, limit, "season DESC,name COLLATE NOCASE,id"
            )

    def product(self, id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT data FROM products WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            return {
                "product": json.loads(row[0]),
                "version": self.version,
                "sections": [
                    {"name": r[0], "card_count": r[1]}
                    for r in db.execute(
                        "SELECT section,count(*) FROM cards WHERE product_id=? AND section IS NOT NULL GROUP BY section ORDER BY section",
                        (id,),
                    )
                ],
                "parallels": [
                    {"name": r[0], "card_count": r[1]}
                    for r in db.execute(
                        "SELECT parallel,count(*) FROM cards WHERE product_id=? AND parallel IS NOT NULL GROUP BY parallel ORDER BY parallel",
                        (id,),
                    )
                ],
            }

    def cards(
        self,
        product_id=None,
        manufacturer=None,
        season=None,
        q=None,
        section=None,
        parallel=None,
        image_state="any",
        offset=0,
        limit=40,
    ):
        if image_state not in {"any", "has_image", "missing_front", "missing_back"}:
            raise ValueError("unknown image state")
        where, params = [], []
        for column, value in [
            ("product_id", product_id),
            ("manufacturer", manufacturer),
            ("season", season),
            ("section", section),
            ("parallel", parallel),
        ]:
            if value is not None:
                where.append(
                    f"{column}=?"
                    + (
                        " COLLATE NOCASE"
                        if column in {"manufacturer", "section", "parallel"}
                        else ""
                    )
                )
                params.append(value)
        if image_state != "any":
            where.append(
                {
                    "has_image": "image_count>0",
                    "missing_front": "front IS NULL",
                    "missing_back": "back IS NULL",
                }[image_state]
            )
        with closing(self._connect()) as db:
            if q and _key(q):
                if product_id is None:
                    covering = (
                        "cards_manufacturer_season"
                        if section is None and parallel is None and image_state == "any"
                        else None
                    )
                    return self._search_cards(db, _key(q), offset, limit, where, params, covering)
                where.append(
                    "EXISTS (SELECT 1 FROM card_search s WHERE s.card_rowid=cards.rowid AND instr(s.search,?)>0)"
                )
                params.append(_key(q))
            return self._page(db, "cards", where, params, offset, limit)

    def _search_cards(self, db, query, offset, limit, where=(), params=(), covering=None):
        """Scan compact text rows, then hydrate only the requested card page.

        Source insertion order is deterministic for an immutable snapshot.
        Keeping that rowid order avoids fetching every wide matching card just
        to sort opaque IDs; only the requested page is hydrated.
        """
        self._bounds(offset, limit)
        clause = " AND ".join(where)
        filters = " AND " + clause if clause else ""
        join = "card_search s CROSS JOIN cards c ON c.rowid=s.card_rowid"
        if clause and covering:
            # Maker/season counts can use their covering metadata index. An
            # intersection avoids random wide-card reads for every text match.
            total = db.execute(
                f"SELECT count(*) FROM cards INDEXED BY {covering} WHERE {clause} "
                "AND rowid IN (SELECT card_rowid FROM card_search WHERE instr(search,?)>0)",
                [*params, query],
            ).fetchone()[0]
        elif clause:
            total = db.execute(
                f"SELECT count(*) FROM {join} WHERE instr(s.search,?)>0{filters}", [query, *params]
            ).fetchone()[0]
        else:
            total = db.execute(
                "SELECT count(*) FROM card_search WHERE instr(search,?)>0", (query,)
            ).fetchone()[0]
        if offset >= total:
            selected = []
        elif clause:
            selected = db.execute(
                f"SELECT s.card_rowid FROM {join} "
                f"WHERE instr(s.search,?)>0{filters} ORDER BY s.card_rowid LIMIT ? OFFSET ?",
                [query, *params, limit, offset],
            )
        else:
            selected = db.execute(
                "SELECT card_rowid FROM card_search WHERE instr(search,?)>0 "
                "ORDER BY card_rowid LIMIT ? OFFSET ?",
                (query, limit, offset),
            )
        matched = [r[0] for r in selected]
        hydrated = {}
        if matched:
            placeholders = ",".join("?" for _ in matched)
            hydrated = {
                r["_rowid"]: self._card_row(r)
                for r in db.execute(
                    f"SELECT rowid AS _rowid,* FROM cards WHERE rowid IN ({placeholders})", matched
                )
            }
        return {
            "items": [hydrated[rowid] for rowid in matched],
            "total": total,
            "offset": offset,
            "limit": limit,
            "version": self.version,
        }

    def card(self, id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            related = []
            if all(
                row[k] is not None and row[k] != ""
                for k in ("product_id", "season", "season_end", "player", "card_number", "section")
            ):
                related = [
                    self._card_row(r)
                    for r in db.execute(
                        "SELECT * FROM cards WHERE id<>? AND product_id=? AND season=? AND season_end=? AND player=? AND card_number=? AND section=? ORDER BY id LIMIT 30",
                        (
                            id,
                            row["product_id"],
                            row["season"],
                            row["season_end"],
                            row["player"],
                            row["card_number"],
                            row["section"],
                        ),
                    )
                ]
            return {
                "card": self._card_row(row),
                "images": [
                    self._media_row(db, r[0])
                    for r in db.execute(
                        "SELECT data FROM media WHERE card_id=? ORDER BY side,id", (id,)
                    )
                ],
                "related_versions": related,
                "version": self.version,
            }

    def media(self, product_id=None, card_id=None, kind="all", offset=0, limit=24):
        if kind not in {"all", "design", "specimen"}:
            raise ValueError("unknown media kind")
        where, params = [], []
        for column, value in [
            ("product_id", product_id),
            ("card_id", card_id),
            ("kind", None if kind == "all" else kind),
        ]:
            if value is not None:
                where.append(f"{column}=?")
                params.append(value)
        with closing(self._connect()) as db:
            return self._page(db, "media", where, params, offset, limit)

    def entities(self, entity_type=None, q=None, offset=0, limit=40):
        self._bounds(offset, limit)
        if entity_type in {None, "card"}:
            with closing(self._connect()) as db:
                query = _key(q)
                if entity_type == "card":
                    page = (
                        self._search_cards(db, query, offset, limit)
                        if query
                        else self._page(db, "cards", [], [], offset, limit)
                    )
                    page["items"] = [self._card_glossary(db, item) for item in page["items"]]
                    return page
                # Terms precede card facts, each with deterministic pagination.
                # This avoids sorting millions of wide card rows for a mixed
                # glossary page while preserving unified counts and aliases.
                where, params = ["entity_type<>'card'"], []
                if query:
                    where.append("instr(search,?)>0")
                    params.append(query)
                clause = " AND ".join(where)
                term_count = db.execute(
                    f"SELECT count(*) FROM entities WHERE {clause}", params
                ).fetchone()[0]
                term_limit = min(limit, max(0, term_count - offset))
                items = []
                if term_limit:
                    items = [
                        json.loads(r[0])
                        for r in db.execute(
                            f"SELECT data FROM entities WHERE {clause} ORDER BY id LIMIT ? OFFSET ?",
                            [*params, term_limit, offset],
                        )
                    ]
                remaining = limit - len(items)
                card_offset = max(0, offset - term_count)
                if remaining:
                    cards = (
                        self._search_cards(db, query, card_offset, remaining)
                        if query
                        else self._page(db, "cards", [], [], card_offset, remaining)
                    )
                    items.extend(self._card_glossary(db, card) for card in cards["items"])
                    card_count = cards["total"]
                elif query:
                    card_count = db.execute(
                        "SELECT count(*) FROM card_search WHERE instr(search,?)>0", (query,)
                    ).fetchone()[0]
                else:
                    card_count = db.execute("SELECT count(*) FROM cards").fetchone()[0]
                return {
                    "items": items,
                    "total": term_count + card_count,
                    "offset": offset,
                    "limit": limit,
                    "version": self.version,
                }

        where, params = [], []
        if entity_type is not None:
            where.append("entity_type=?")
            params.append(entity_type)
        if q and _key(q):
            where.append("instr(search,?)>0")
            params.append(_key(q))
        with closing(self._connect()) as db:
            return self._page(db, "entities", where, params, offset, limit)

    @staticmethod
    def _card_glossary(db, card):
        original = db.execute(
            "SELECT data FROM entities WHERE id=? AND entity_type='card'", (card["id"],)
        ).fetchone()
        if original:
            return json.loads(original[0])
        return {
            "id": card["id"],
            "entity_type": "card",
            "canonical_name": card["name"],
            "aliases": [],
            "description": None,
            "source_url": card["sources"][0]["url"] if card["sources"] else None,
        }

    def media_file(self, id):
        with closing(self._connect()) as db:
            row = db.execute("SELECT path,sha256 FROM media WHERE id=?", (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            path = Path(row[0]).resolve()
            if not path.is_relative_to(self.data_root):
                raise KeyError(id)
            return path, row[1]
