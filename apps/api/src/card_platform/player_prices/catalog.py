"""Published official membership for prices; physical variants need sale evidence."""

from collections import Counter
from pathlib import Path
import re

from card_platform.domain.player_aliases import matching_player_aliases
from card_platform.domain.season import explicit_seasons
from card_platform.ingestion.cardhobby import _contains_phrase, _insert_aliases, _normalized
from card_platform.knowledge_card_catalog import ScopedCardCatalog, text_key, product_key
from card_platform.player_prices.subjects import canonical_subject_name


BASE_SECTIONS = {"base", "basecards", "baseset"}
# Reviewed seller kind vocabulary can veto an explicit contradiction, but
# cannot establish membership in a product/release or supply physical fields.
TITLE_KIND_CONFLICT_NAMES = {
    "Finalists": ("Finalists",),
    "Extraterrestrial Talent": ("Extraterrestrial Talent", "外星天才"),
}


def _official(row):
    attrs = row.get("attributes") or {}
    return (row.get("review_status", "approved") == "approved"
            and attrs.get("authority") == "official_checklist"
            and str(attrs.get("source_url") or "").startswith("https://")
            and bool(re.fullmatch(r"[a-f0-9]{64}", str(attrs.get("source_checksum") or "")))
            and bool(str(attrs.get("source_locator") or "").strip()))


def _ordinary_base(attrs):
    return (not attrs.get("insert") and text_key(attrs.get("section")) in BASE_SECTIONS
            and text_key(attrs.get("parallel")) in {"", *BASE_SECTIONS}
            and attrs.get("serial_total") is None and attrs.get("print_run") is None)


def _section_key(value):
    words = re.sub(r"[-–—_/]+", " ", str(value or ""))
    return text_key(re.sub(r"\bprizms\b", "Prizm", words, flags=re.I))


def _product(attrs):
    # Retain Topps Chrome; Panini's familiar product label is simply Prizm etc.
    return re.sub(r"^Panini\s+", "", str(attrs.get("product") or ""), flags=re.I).strip()


def _scope(attrs):
    return (text_key(attrs.get("brand")), product_key(attrs.get("product")),
            attrs.get("season_start"), attrs.get("season_end"))


def _insert_vocabulary(pool):
    names = {}
    for members in pool.values():
        for row in members:
            attrs = row["attributes"]
            if _official(row) and attrs.get("insert"):
                names.setdefault(_scope(attrs), set()).add(attrs["insert"])
    return names


class PriceCardCatalog:
    def __init__(self, snapshot, *, artifact_root=None):
        self.version = snapshot.get("version")
        self.catalog = ScopedCardCatalog(
            snapshot.get("entities", ()), version=self.version,
            artifacts=snapshot.get("official_artifacts", ()), artifact_root=artifact_root,
        )
        self._touched_artifacts = set()
        # Compact immutable-version vocabulary survives full-row LRU eviction.
        self._artifact_insert_names = {}

    def session(self):
        """Share immutable membership/cache, with errors scoped to this refresh."""
        child = object.__new__(type(self))
        child.version, child.catalog = self.version, self.catalog
        child._touched_artifacts = set()
        child._artifact_insert_names = self._artifact_insert_names
        return child

    def _observe_artifact(self, path, pool):
        if path not in self._artifact_insert_names:
            self._artifact_insert_names[path] = _insert_vocabulary(pool)

    def _lookup(self, identity, aliases):
        scoped = [identity, *[
            {**identity, "product_name": m["product"], "brand_name": m.get("catalog_brand",m["brand"])}
            for m in self.catalog.mappings_for(identity)
        ]]
        for candidate in scoped:
            key = self.catalog._catalog_product_key(candidate)
            start, end = candidate.get("season_start"), candidate.get("season_end")
            for artifact in self.catalog.artifacts:
                same_season = ((start, end) == (artifact.get("season_start"), artifact.get("season_end"))
                               if end is not None and end != start else
                               start in {artifact.get("season_start"), artifact.get("season_end")})
                if (self.catalog._catalog_product_key(artifact, require_source=True) == key
                        and same_season):
                    self._touched_artifacts.add(artifact["path"])
        return self.catalog.lookup(identity, player_aliases=aliases,
                                   artifact_observer=self._observe_artifact)

    @property
    def errors(self):
        return {path: error for path, error in self.catalog.artifact_errors.items()
                if path in self._touched_artifacts}

    def _aliases(self, name):
        canonical = canonical_subject_name(name)
        return tuple(dict.fromkeys([name, canonical, *matching_player_aliases(canonical.casefold())]))

    def _proof(self, row):
        attrs = row["attributes"]
        return dict(knowledge_version=self.version, entity_id=str(row["id"]),
                    source_url=attrs["source_url"], source_checksum=attrs["source_checksum"],
                    source_locator=attrs["source_locator"],
                    fields=["player_name", "brand_name", "product_name", "season_start",
                            "season_end", "card_number", "insert_name"])

    def confirm_title(self, identity, sale):
        """Check an anchored title, without supplying any missing physical finish."""
        return self._confirm(identity, sale, physical=False)

    def confirm_image(self, identity, sale):
        """Caller has verified the independent front fields of a bound receipt."""
        return self._confirm(identity, sale, physical=True)

    def _named_insert_kinds(self, title, rows):
        scopes = {_scope(row["attributes"]) for row in rows}
        names = set(TITLE_KIND_CONFLICT_NAMES)
        # The scoped lookup has loaded and checksum-verified these artifacts.
        # A contradictory named kind can have a different collector number,
        # so inspect this product/release's vocabulary, not just this number.
        vocabularies = [_insert_vocabulary(self.catalog.index), *[
            vocabulary for path, vocabulary in self._artifact_insert_names.items()
            if path not in self.catalog.artifact_errors]]
        for vocabulary in vocabularies:
            for scope in scopes:
                names.update(vocabulary.get(scope, ()))
        normalized = _normalized(title)
        spans = set()
        for name in names:
            for alias in TITLE_KIND_CONFLICT_NAMES.get(name, _insert_aliases({"insert_name": name})):
                phrase = _normalized(alias)
                if not phrase:
                    continue
                pattern = ((r"(?<![a-z0-9])" if phrase[0].isascii() else "")
                           + re.escape(phrase)
                           + (r"(?![a-z0-9])" if phrase[-1].isascii() else ""))
                spans.update((match.start(), match.end(), text_key(name))
                             for match in re.finditer(pattern, normalized))
        # Rookie Penmanship includes Penmanship; that nested occurrence is
        # one declaration. A separate Penmanship occurrence still conflicts.
        return {name for start, end, name in spans if not any(
            left <= start and end <= right and right - left > end - start
            for left, right, _ in spans)}

    def _confirm(self, identity, sale, *, physical):
        from card_platform.player_prices.discovery import identity_title

        if not identity.get("parallel_name"):
            return None
        title = identity_title(sale.title)
        scoped = identity
        title_seasons = explicit_seasons(title)
        if (identity.get("season_end") in (None, identity.get("season_start"))
                and len(title_seasons) == 1):
            start, end = next(iter(title_seasons))
            if identity.get("season_start") == start:
                scoped = {**identity,"season_start":start,"season_end":end}
        # Core membership and contradictory named sections must remain visible
        # even when the proposed finish is wrong. Physical matching follows.
        core_scope = {**scoped,"parallel_name":None,"serial_total":None}
        rows = [row for row in self._lookup(core_scope, self._aliases(
            identity.get("player_name") or "")) if _official(row)]
        if str(identity.get("parallel_name")).casefold() in {"base", "普卡", "基础卡"}:
            title_key = _section_key(title)
            # Read the exact card's published sections instead of guessing a
            # finite family list. "Base" in Base Prizms Fast Break/Ice/etc is
            # a parent section, not evidence for an ordinary physical base.
            for member in rows:
                section = _section_key(member["attributes"].get("section"))
                if (section.startswith("baseprizm") and section != "baseprizm"
                        and section in title_key):
                    return None
        if physical:
            observed = text_key(identity.get("insert_name"))
            named = self._named_insert_kinds(title, rows)
            if named and named != {observed}:
                return None
            if (observed and not named and re.search(
                    r"(?<![A-Za-z])base(?![A-Za-z])|普卡|基础卡", title, re.I)):
                return None
            rows = [row for row in rows if (
                text_key(row["attributes"].get("insert")) == observed if observed
                else _ordinary_base(row["attributes"]))]
        else:
            inserts = [row for row in rows if row["attributes"].get("insert") and any(
                _contains_phrase(title, alias) for alias in _insert_aliases(
                    {"insert_name": row["attributes"]["insert"]}))]
            rows = inserts or [row for row in rows if _ordinary_base(row["attributes"])
                              and not identity.get("insert_name")]
        # Parallel rows/variation sections do not prove ordinary base membership.
        kinds = {}
        for row in rows:
            attrs = row["attributes"]
            key = (text_key(attrs.get("brand")), product_key(attrs.get("product")),
                   attrs.get("season_start"), attrs.get("season_end"),
                   text_key(attrs.get("player")), text_key(attrs.get("card_number")),
                   text_key(attrs.get("insert")))
            kinds.setdefault(key, row)
        if len(kinds) != 1:
            return None
        row = next(iter(kinds.values()))
        attrs = row["attributes"]
        season = (attrs.get("season_start"), attrs.get("season_end"))
        if identity.get("season_end") in (None, identity.get("season_start")):
            # A full source title or a published release mapping must bind the
            # season, rather than borrowing an adjacent year's card number.
            if season not in explicit_seasons(title) and not self.catalog.mappings_for(identity):
                return None
        parallel = identity["parallel_name"]
        # The existing formal matcher explicitly accepts 银折/Silver for a
        # Silver Prizm target. Bind that spelling to this official product,
        # retaining the full name for its technology-conflict checks. Do not
        # apply the equivalence to Chrome, Optic or more specific finishes.
        if (text_key(attrs.get("brand")) == "panini" and product_key(attrs.get("product")) == "prizm"
                and str(parallel).casefold() in {"silver", "red", "blue", "gold"}):
            parallel = str(parallel).title() + " Prizm"
        return {**identity, "player_name": canonical_subject_name(identity["player_name"]),
                "brand_name": attrs["brand"], "product_name": _product(attrs),
                "season_start": season[0], "season_end": season[1],
                "card_number": str(attrs["card_number"]), "insert_name": attrs.get("insert"),
                "parallel_name": parallel,
                "catalog_evidence": self._proof(row)}

    def discovery_targets(self, name, sales, *, limit=3):
        """Suggest exact-number queries from verified artifacts, never price samples."""
        from card_platform.player_prices.discovery import PRODUCTS, identity_title

        scopes = Counter()
        for sale in sales:
            title = identity_title(sale.get("title") or sale.get("raw_title") or "")
            seasons = explicit_seasons(title)
            products = [product for product, aliases in PRODUCTS.items()
                        if any(re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])",
                                         title, re.I) for alias in aliases)]
            if "Donruss Optic" in products:
                products = [p for p in products if p != "Donruss"]
            if len(seasons) == len(products) == 1:
                start, end = next(iter(seasons))
                scopes[(products[0], start, end)] += 1
        aliases = self._aliases(name)
        players = {text_key(alias) for alias in aliases}
        targets = []
        seen = set()
        for (product, start, end), _ in scopes.most_common():
            identity = dict(player_name=name, product_name=product,
                            brand_name="Topps" if product.startswith("Topps") else "Panini",
                            season_start=start, season_end=end, card_number="__discovery__")
            # Reuse the catalog's checksum/path/scope verifier and bounded LRU.
            self._lookup(identity, aliases)
            pools = [self.catalog.index, *self.catalog.artifact_indexes.values()]
            for pool in pools:
                for (key, _number), rows in pool.items():
                    if key != product_key(product):
                        continue
                    for row in rows:
                        attrs = row["attributes"]
                        if (not _official(row) or not _ordinary_base(attrs)
                                or text_key(attrs.get("player")) not in players
                                or (attrs.get("season_start"), attrs.get("season_end")) != (start, end)):
                            continue
                        candidate = {**identity, "product_name": _product(attrs),
                                     "brand_name": attrs["brand"], "card_number": str(attrs["card_number"])}
                        key = (product_key(candidate["product_name"]), start, end, candidate["card_number"])
                        if key in seen:
                            continue
                        seen.add(key)
                        # No finish/price/grade inferred. These are query seeds.
                        targets.append(dict(identity=candidate, discovery_only=True,
                                            catalog_evidence=self._proof(row)))
                        if len(targets) >= limit:
                            return targets
        return targets


_loaded_version = None
_loaded_catalog = None


async def published_catalog(c):
    """Read only the current cloud publication, caching its immutable snapshot."""
    global _loaded_version, _loaded_catalog
    row = await (await c.execute(
        """SELECT kw.published_version_id,kv.snapshot
        FROM knowledge_workspace kw
        LEFT JOIN knowledge_version kv ON kv.id=kw.published_version_id
          AND kv.id IS DISTINCT FROM %s::uuid
        WHERE kw.name='Card Platform'""", (_loaded_version,),
    )).fetchone()
    version = row["published_version_id"] if row else None
    if version is not None and version == _loaded_version and _loaded_catalog is not None:
        return _loaded_catalog
    snapshot = row["snapshot"] if row and row["snapshot"] else {}
    cloud_root = Path("/app/data/official-checklists/artifacts")
    catalog = PriceCardCatalog(snapshot, artifact_root=cloud_root if cloud_root.is_dir() else None)
    if version is not None:
        _loaded_version, _loaded_catalog = version, catalog
    return catalog
