"""Product-scoped official facts; missing fields are never global alias guesses."""

from collections import defaultdict, OrderedDict
import gzip
import json
from hashlib import sha256
from pathlib import Path
import re
import unicodedata

from card_platform.domain.season import explicit_seasons


def text_key(value: object) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in value if c.isalnum() and not unicodedata.combining(c))


def product_key(value: object) -> str:
    value = str(value or "").casefold().replace("®", "")
    value = re.sub(r"\b(?:19|20)\d{2}(?:\s*[-/]\s*\d{2,4})?\b", "", value)
    value = re.sub(r"\b(?:topps|panini|basketball)\b", "", value)
    without_publisher = re.sub(r"\b(?:upper\s+deck|ud)\b", "", value).strip()
    return text_key(without_publisher or value)


class ScopedCardCatalog:
    def __init__(self, entities=(), *, version=None, artifacts=(), artifact_root=None):
        self.copyright_year_labels = [
            e['attributes']['copyright_year_labels'] for e in entities
            if e.get('entity_type') == 'product'
            and e.get('review_status', 'approved') == 'approved'
            and isinstance((e.get('attributes') or {}).get('copyright_year_labels'), dict)
        ]
        self.release_label_evidence = [
            e.get('attributes') or {} for e in entities
            if e.get('entity_type') == 'product'
            # Published snapshots already filter approved entities and omit
            # this field. Explicit draft/pending records remain ineligible.
            and e.get('review_status', 'approved') == 'approved'
            and isinstance((e.get('attributes') or {}).get('release_labels'), list)
        ]
        self.manufacturer_releases = [
            e.get("attributes") or {} for e in entities
            if e.get("entity_type") == "product"
            and isinstance((e.get("attributes") or {}).get("manufacturer_named_release"), dict)
            and (e["attributes"]["manufacturer_named_release"].get("review_status") == "approved")
            and e.get("review_status", "approved") == "approved"
        ]
        self.mappings = [m for e in entities for m in (e.get("attributes") or {}).get("catalog_mappings", []) if m.get("source_url")]
        self.parallel_options = [o for e in entities for o in (e.get("attributes") or {}).get("parallel_options", []) if o.get("source_url")]
        self.parallel_term_targets = {
            (text_key(o.get('brand')), product_key(o.get('product')),
             o.get('season_start'), o.get('season_end'), text_key(o.get('official_name')))
            for o in self.parallel_options if o.get('seller_terms')
        }
        self.parallel_parent_options = defaultdict(list)
        for option in self.parallel_options:
            section = option.get("availability_scope")
            if section:
                self.parallel_parent_options[self._parallel_source_scope_key(option, section)].append(option)
        self.version = version
        self.artifacts = artifacts
        self.artifact_root = Path(artifact_root or Path(__file__).resolve().parents[4] / "data/official-checklists/artifacts")
        self.artifact_indexes = OrderedDict()
        self.artifact_errors = {}
        self.index = defaultdict(list)
        for entity in entities:
            if entity.get("entity_type") != "card":
                continue
            attrs = entity.get("attributes") or {}
            if not attrs.get("source_url"):
                continue
            key = (self._catalog_product_key(attrs, require_source=True), text_key(attrs.get("card_number")))
            if all(key):
                self.index[key].append(entity)

    @staticmethod
    def _release_label(value):
        return re.sub(r"\b(?:19|20)\d{2}(?:\s*[-‐‑‒–—−/]\s*\d{2,4})?\b", "", str(value or ""))

    @classmethod
    def _manufacturer_label(cls, value, brand):
        if not brand:
            return False
        label = cls._release_label(value)
        label = re.sub(r"\b(?:nba|basketball|football|baseball|hockey|soccer)\b", "", label, flags=re.I)
        return text_key(label) == text_key(brand)

    def _catalog_product_key(self, attrs, *, require_source=False):
        """Keep self-named makers separate from globally normalized products.

        A full label is insufficient by itself. A published, reviewed release
        declaration and its original source bind the catalog rows to this key.
        Bare maker labels never obtain it, including through query mappings.
        """
        value = attrs.get("product_name", attrs.get("product"))
        brand = attrs.get("brand_name", attrs.get("brand"))
        if not self._manufacturer_label(value, brand):
            return product_key(value)
        start, end = attrs.get("season_start"), attrs.get("season_end")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            return ""
        if explicit_seasons(value) != {(start, end)}:
            return ""
        for release in self.manufacturer_releases:
            sport = release.get("sport")
            label = release.get("product")
            if (not sport or text_key(sport) not in text_key(value)
                    or text_key(sport) not in text_key(label)
                    or text_key(brand) != text_key(release.get("brand"))
                    or (start, end) != (release.get("season_start"), release.get("season_end"))
                    or explicit_seasons(label) != {(start, end)}
                    or text_key(self._release_label(value)) != text_key(self._release_label(label))
                    or release.get("authority") not in {"official_product", "official_checklist"}
                    or not str(release.get("source_url") or "").startswith("https://")
                    or not re.fullmatch(r"[0-9a-f]{64}", str(release.get("source_checksum") or ""))):
                continue
            if attrs.get("sport") and text_key(attrs["sport"]) != text_key(sport):
                continue
            if require_source:
                sources = [release, *(release.get("supporting_sources") or [])]
                if attrs.get("authority") != "official_checklist" or not any(
                    isinstance(source, dict)
                    and source.get("authority") in {"official_product", "official_checklist"}
                    and str(source.get("source_url") or "").startswith("https://")
                    and re.fullmatch(r"[0-9a-f]{64}", str(source.get("source_checksum") or ""))
                    and source.get("source_url") == attrs.get("source_url")
                    and source.get("source_checksum") == attrs.get("source_checksum")
                    for source in sources
                ):
                    continue
            return f"manufacturer-release:{text_key(brand)}:{text_key(sport)}:{start}:{end}"
        return ""

    def mappings_for(self, identity):
        start, end = identity.get("season_start"), identity.get("season_end")
        return [m for m in self.mappings
                if text_key(identity.get("brand_name")) == text_key(m.get("brand"))
                and product_key(identity.get("product_name")) == product_key(m.get("alias"))
                and start is not None
                and ((start, end) == (m.get("season_start"), m.get("season_end"))
                     if end is not None and end != start else start in {m.get("season_start"), m.get("season_end")})]

    def parallel_candidates(self, identity):
        """Scoped alternatives, never evidence that a physical card is that parallel."""
        identities = [identity]
        for mapping in self.mappings_for(identity):
            candidate = identity | {"product_name": mapping["product"], "brand_name": mapping.get("catalog_brand", mapping["brand"]), "season_start": mapping["season_start"], "season_end": mapping["season_end"]}
            insert = identity.get("insert_name") or identity.get("insert")
            for alias, canonical in mapping.get("insert_aliases", {}).items():
                if text_key(insert) == text_key(alias):
                    candidate["insert_name"] = canonical
            identities.append(candidate)
        scopes = [self._parallel_target_sections(candidate) for candidate in identities]
        found = []
        for option in self.parallel_options:
            for candidate, sections in zip(identities, scopes):
                start, end = candidate.get("season_start"), candidate.get("season_end")
                if text_key(candidate.get("brand_name")) != text_key(option.get("brand")) or product_key(candidate.get("product_name")) != product_key(option.get("product")):
                    continue
                if start is None or ((start, end) != (option.get("season_start"), option.get("season_end")) if end is not None and end != start else start not in {option.get("season_start"), option.get("season_end")}):
                    continue
                if candidate.get("serial_total"):
                    if option.get("serial_total") is not None and str(candidate["serial_total"]) != str(option["serial_total"]):
                        continue
                    if option.get("serial_total") is None and option.get("serial_status") == "confirmed_unnumbered":
                        continue
                scoped = self._scoped_parallel_option(option, candidate, sections)
                if scoped is None:
                    continue
                found.append(scoped)
                break
        return found

    def matching_parallel_terms(self, identity):
        """Return audited vocabulary for an already named, numbered variant.

        Official availability and market terminology have separate provenance.
        Neither supplies an unknown physical finish or an absent print run.
        """
        target_name = identity.get('parallel_name') or identity.get('parallel')
        total = identity.get('serial_total') or identity.get('print_run')
        if not target_name or not total:
            return ()
        keys = {(text_key(identity.get('brand_name')), product_key(identity.get('product_name')),
                 identity.get('season_start'), identity.get('season_end'), text_key(target_name))}
        keys.update((text_key(m.get('catalog_brand', m['brand'])), product_key(m['product']),
                     m['season_start'], m['season_end'], text_key(target_name))
                    for m in self.mappings_for(identity))
        if not keys & self.parallel_term_targets:
            return ()
        target = {**identity, 'serial_total': total}
        rows = self.lookup(target)
        terms = []
        for option in self.parallel_candidates(target):
            if (option.get('review_status') != 'approved'
                    or option.get('authority') not in {'official_odds', 'official_checklist'}
                    or option.get('scope_verification') != 'verified'
                    or text_key(option.get('official_name')) != text_key(target_name)
                    or not str(option.get('source_url') or '').startswith('https://')
                    or not option.get('source_locator')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(option.get('source_checksum') or ''))):
                continue
            insert = text_key(identity.get('insert_name') or identity.get('insert'))
            if not any(
                attrs.get('authority') == 'official_checklist'
                and attrs.get('source_url') == option.get('catalog_source_url')
                and attrs.get('source_checksum') == option.get('catalog_source_checksum')
                and re.fullmatch(r'[a-f0-9]{64}', str(attrs.get('source_checksum') or ''))
                and attrs.get('source_locator')
                and (insert in {text_key(attrs.get('insert')), text_key(attrs.get('section'))}
                     if insert else not attrs.get('insert'))
                for attrs in (row['attributes'] for row in rows)
            ):
                continue
            candidates = option.get('seller_terms')
            if not isinstance(candidates, list):
                continue
            for term in candidates:
                if (not isinstance(term, dict) or term.get('review_status') != 'approved'
                        or not isinstance(term.get('value'), str)
                        or not 2 <= len(term['value'].strip()) <= 80
                        or not str(term.get('source_url') or '').startswith('https://')
                        or not term.get('source_locator')
                        or not re.fullmatch(r'[a-f0-9]{64}', str(term.get('source_checksum') or ''))):
                    continue
                if (option.get('serial_verification') != 'verified'
                        and not self._specimen_parallel_term_matches(option, term, total)):
                    continue
                pair = (term['value'].strip(), option['official_name'])
                if pair not in terms:
                    terms.append(pair)
        return tuple(terms)

    @staticmethod
    def _specimen_parallel_term_matches(option, term, total):
        """A reviewed specimen scopes vocabulary, never an official print run.

        The market front/back pair must belong to the term's source, and its
        naming review must cite a reviewed pattern image of this exact option.
        Current target/source pairing is still required by the formal gate.
        """
        if (option.get('serial_total') is not None
                or option.get('serial_status') == 'confirmed_unnumbered'
                or type(term.get('observed_serial_total')) is not int
                or term['observed_serial_total'] <= 0
                or str(term['observed_serial_total']) != str(total)):
            return False
        references = option.get('reference_images')
        checksums = term.get('pattern_reference_checksums')
        evidence = term.get('image_evidence')
        if not all(isinstance(value, list) for value in (references, checksums, evidence)):
            return False
        if not any(isinstance(ref, dict) and ref.get('side') == 'front'
                   and ref.get('review_status') == 'cross_checked_visual_reference'
                   and str(ref.get('source_url') or '').startswith('https://')
                   and str(ref.get('image_url') or '').startswith('https://')
                   and re.fullmatch(r'[a-f0-9]{64}', str(ref.get('sha256') or ''))
                   and ref['sha256'] in checksums for ref in references):
            return False
        reviewed = {image.get('side'): image for image in evidence if isinstance(image, dict)
                    and image.get('review_status') == 'approved'
                    and image.get('source_url') == term.get('source_url')
                    and str(image.get('image_url') or '').startswith('https://')
                    and re.fullmatch(r'[a-f0-9]{64}', str(image.get('sha256') or ''))
                    and isinstance(image.get('observations'), str) and image['observations'].strip()}
        return (all(side in reviewed for side in ('front', 'back'))
                and reviewed['front']['sha256'] != reviewed['back']['sha256'])

    def _parallel_target_sections(self, identity):
        sections = set()
        rows = [row["attributes"] for row in self.lookup(identity)
                if not self._autograph_role_conflicts(identity, row["attributes"])]
        source_sections = {self._parallel_source_scope_key(attrs, attrs.get("section")) for attrs in rows}
        for attrs in rows:
            section = attrs.get("section")
            parents = set()
            # Only an exact original card-set row, in the same release/source,
            # can supply its parent. Never strip a guessed parallel suffix.
            for option in self.parallel_parent_options.get(self._parallel_source_scope_key(attrs, section), ()):
                parents.update(self._parallel_available_sections(option, identity) or ())
            parallel = text_key(attrs.get("parallel"))
            normalized = text_key(section)
            if not parents and parallel and normalized.endswith(parallel) and normalized != parallel:
                parent = normalized[:-len(parallel)]
                if self._parallel_source_scope_key(attrs, parent) in source_sections:
                    parents.add(parent)
            sections.update(parents or {text_key(section)})
        sections.discard("")
        if not sections:
            insert = text_key(identity.get("insert_name") or identity.get("insert"))
            # An empty/base recognition does not distinguish Base Rookies from
            # Base Finals MVPs. A scoped official card row can distinguish them.
            if insert and insert not in {"base", "basecards", "baseset"}:
                sections.add(insert)
        return sections

    @staticmethod
    def _parallel_source_scope_key(attrs, section):
        return (text_key(attrs.get("brand")), product_key(attrs.get("product")),
                attrs.get("season_start"), attrs.get("season_end"),
                attrs.get("source_url"), attrs.get("source_checksum"), text_key(section))

    @staticmethod
    def _autograph_role_conflicts(identity, attrs):
        expected = identity.get("is_autograph")
        if not isinstance(expected, bool):
            return False
        actual = attrs.get("is_autograph")
        if not isinstance(actual, bool):
            labels = [attrs.get("section"), attrs.get("availability_scope"),
                      *(attrs.get("availability_sections") or [])]
            # A named official autograph family is positive role evidence.
            # Absence of these words never establishes a non-autograph role.
            actual = True if any(re.search(r"\b(?:autographs?|signatures)\b", str(label), re.I)
                                 and not re.search(r"\bfacsimile\b", str(label), re.I)
                                 for label in labels if label) else None
        return isinstance(actual, bool) and actual != expected

    @staticmethod
    def _parallel_available_sections(option, identity):
        if ScopedCardCatalog._autograph_role_conflicts(identity, option):
            return None
        declared = option.get("availability_sections") or []
        allowed_sections = {text_key(value) for value in declared if isinstance(value, str)} if isinstance(declared, list) else set()
        if not allowed_sections:
            legacy = text_key(option.get("availability_scope"))
            name = text_key(option.get("official_name"))
            if name and legacy.endswith(name) and legacy != name:
                allowed_sections = {legacy[:-len(name)]}
        players = {text_key(identity.get("player_name")),
                   *(text_key(value) for value in identity.get("player_name_aliases", []) or [])} - {""}
        available_players = {text_key(value) for value in option.get("availability_players", []) or []}
        if available_players and players and not players & available_players:
            return None
        members = option.get("availability_members") or []
        if members:
            # Preserve section/player pairs; their Cartesian product is not an
            # official availability statement. List ordinal is not card number.
            matching = [member for member in members if isinstance(member, dict)
                        and text_key(member.get("player")) in players]
            if not matching:
                return None
            member_sections = {text_key(member.get("section")) for member in matching} - {""}
            if allowed_sections and member_sections and not allowed_sections & member_sections:
                return None
            allowed_sections = allowed_sections & member_sections if allowed_sections else member_sections
        return allowed_sections

    @staticmethod
    def _scoped_parallel_option(option, identity, target_sections):
        allowed_sections = ScopedCardCatalog._parallel_available_sections(option, identity)
        if allowed_sections is None:
            return None
        if target_sections and allowed_sections and not target_sections & allowed_sections:
            return None
        verified = bool(target_sections and allowed_sections and target_sections <= allowed_sections)
        return {**option,
                "source_scope_verification": option.get("scope_verification"),
                "scope_verification": "verified" if verified else "unverified",
                "serial_verification": "verified" if identity.get("serial_total") is not None
                    and option.get("serial_total") is not None
                    and str(identity["serial_total"]) == str(option["serial_total"]) else "unverified"}

    def release_labels_for(self, identity):
        """Approved publisher naming evidence, scoped to a release, not a card.

        A product page can prove its own names without a complete checklist.
        It cannot populate the card index or supply physical identity fields.
        """
        result = []
        for attrs in self.release_label_evidence:
            if (attrs.get('authority') != 'official_product'
                    or attrs.get('distributed_release_labels') is not True
                    or not str(attrs.get('source_url') or '').startswith('https://')
                    or not attrs.get('source_locator')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(attrs.get('source_checksum') or ''))
                    or not attrs.get('brand') or not attrs.get('product')
                    or not isinstance(attrs.get('season_start'), int)
                    or not isinstance(attrs.get('season_end'), int)
                    or text_key(attrs['brand']) != text_key(identity.get('brand_name'))
                    or text_key(attrs['product']) != text_key(identity.get('product_name'))
                    or (attrs['season_start'], attrs['season_end']) !=
                        (identity.get('season_start'), identity.get('season_end'))):
                continue
            result.append(attrs)
        return tuple(result)

    def discovery_vocabulary(self, identity):
        """Publisher vocabulary for discovery, never identity equivalence.

        Exact card lookup supplies brand/product/season/subject/number scope.
        This deliberately does not feed matching_product_aliases or enrichment.
        """
        result = {'player': [], 'product': [], 'insert': []}
        # Release-level vocabulary applies to the whole release. It has no
        # subject authority and must not become a global product alias.
        for attrs in self.release_labels_for(identity):
            vocabulary = attrs.get('query_aliases')
            values = vocabulary.get('product', []) if isinstance(vocabulary, dict) else []
            if isinstance(values, list):
                result['product'].extend(value.strip() for value in values
                    if isinstance(value, str) and 0 < len(value.strip()) <= 100
                    and value.strip() not in result['product'])
        for row in self.lookup(identity):
            attrs = row['attributes']
            if (attrs.get('authority') != 'official_checklist'
                    or not attrs.get('source_url') or not attrs.get('source_locator')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(attrs.get('source_checksum') or ''))):
                continue
            vocabulary = attrs.get('query_aliases')
            if not isinstance(vocabulary, dict):
                continue
            for kind in result:
                values = vocabulary.get(kind, [])
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, str) and 0 < len(value.strip()) <= 100 and value.strip() not in result[kind]:
                        result[kind].append(value.strip())
        return {kind: tuple(values[:3]) for kind, values in result.items()}

    def query_products(self, identity):
        # Correcting an identity to the catalog's canonical product must not
        # discard the original release aliases used in seller titles. Reverse
        # only mappings already bound to this card's official source and year.
        return tuple(dict.fromkeys([
            *(m["product"] for m in self.mappings_for(identity)),
            *self.matching_product_aliases(identity),
        ]))

    def matching_product_aliases(self, identity):
        """Reverse official product mappings only within an anchored card scope.

        A query mapping may name a parent/child branch. It is not automatically
        an equivalence for matching, and an unrelated source cannot lend one.
        """
        rows = self.lookup(identity)
        aliases = []
        for mapping in self.mappings:
            if mapping.get("child_series") or mapping.get("insert_aliases"):
                continue
            if (text_key(mapping.get("brand")) != text_key(identity.get("brand_name"))
                    or product_key(mapping.get("product")) != product_key(identity.get("product_name"))
                    or (mapping.get("season_start"), mapping.get("season_end"))
                    != (identity.get("season_start"), identity.get("season_end"))):
                continue
            if any(
                attrs.get("source_url") == mapping.get("source_url")
                and attrs.get("source_checksum")
                and attrs.get("authority") == "official_checklist"
                and (attrs.get("season_start"), attrs.get("season_end"))
                    == (mapping.get("season_start"), mapping.get("season_end"))
                for attrs in (row["attributes"] for row in rows)
            ):
                aliases.append(mapping.get("alias"))
        return tuple(dict.fromkeys(alias for alias in aliases if alias))

    def lookup(self, identity: dict, *, player_aliases=(), _mapped=False) -> list[dict]:
        product = self._catalog_product_key(identity)
        if not product:
            return []
        if not _mapped:
            mappings = self.mappings_for(identity)
            if mappings:
                found = {}
                for mapping in mappings:
                    scoped = identity | {"product_name": mapping["product"], "brand_name": mapping.get("catalog_brand", mapping["brand"])}
                    insert = identity.get("insert_name") or identity.get("insert")
                    for alias, canonical in mapping.get("insert_aliases", {}).items():
                        if text_key(insert) == text_key(alias):
                            scoped["insert_name"] = canonical
                    for row in self.lookup(scoped, player_aliases=player_aliases, _mapped=True):
                        attrs = row["attributes"]
                        if (attrs.get("season_start"), attrs.get("season_end")) != (mapping.get("season_start"), mapping.get("season_end")):
                            continue
                        section = text_key(attrs.get("section"))
                        child = text_key(mapping.get("child_series"))
                        if child and not (section == child or section.startswith("base" + child)):
                            continue
                        found[row["id"]] = row
                return list(found.values())
        number = text_key(identity.get("card_number"))
        player = text_key(identity.get("player_name"))
        start, end = identity.get("season_start"), identity.get("season_end")
        if not all((product, number, player, start)):
            return []
        players = {player, *(text_key(x) for x in player_aliases)}
        pool = list(self.index.get((product, number), ()))
        for artifact in self.artifacts:
            if self._catalog_product_key(artifact, require_source=True) != product or start not in {artifact.get("season_start"), artifact.get("season_end")}:
                continue
            path = artifact["path"]
            if path in self.artifact_indexes:
                self.artifact_indexes.move_to_end(path)
                pool.extend(self.artifact_indexes[path].get((product, number), ()))
                continue
            try:
                target = (self.artifact_root / path).resolve()
                if not target.is_relative_to(self.artifact_root.resolve()):
                    raise ValueError("Invalid official artifact path")
                raw = target.read_bytes()
                if sha256(raw).hexdigest() != artifact["checksum"]:
                    raise ValueError("Official artifact checksum mismatch")
                entities = json.loads(gzip.decompress(raw))
                staged = defaultdict(list)
                for entity in entities:
                    attrs = entity["attributes"]
                    if entity["entity_type"] != "card" or not entity["id"] or not attrs.get("source_url"):
                        raise ValueError("Invalid official card entity")
                    if self._catalog_product_key(attrs, require_source=True) != product or (attrs.get("season_start"), attrs.get("season_end")) != (artifact.get("season_start"), artifact.get("season_end")):
                        raise ValueError("Official artifact scope mismatch")
                    staged[(product, text_key(attrs.get("card_number")))].append(entity)
                self.artifact_indexes[path] = staged
                while len(self.artifact_indexes) > 8:
                    self.artifact_indexes.popitem(last=False)
                pool.extend(staged.get((product, number), ()))
                self.artifact_errors.pop(path, None)
            except Exception as exc:
                self.artifact_errors[path] = str(exc)
        found = []
        for entity in pool:
            attrs = entity["attributes"]
            if text_key(attrs.get("player")) not in players:
                continue
            allowed_brands = {text_key(attrs.get("brand")), *(text_key(b) for b in attrs.get("brand_aliases", []))}
            if identity.get("brand_name") and text_key(identity["brand_name"]) not in allowed_brands:
                continue
            candidate_start, candidate_end = attrs.get("season_start"), attrs.get("season_end")
            if end is not None and end != start:
                if (start, end) != (candidate_start, candidate_end):
                    continue
            elif start not in {candidate_start, candidate_end}:
                continue
            insert = identity.get("insert_name") or identity.get("insert")
            if insert:
                expected = text_key(insert)
                actual = text_key(attrs.get("insert"))
                section = text_key(attrs.get("section"))
                is_base = expected in {"base", "basecards", "baseset"} and not actual and section in {"base", "basecards", "baseset"}
                if not is_base and expected != actual and section != "base" + expected:
                    continue
            if any(
                identity.get(field) not in (None, "")
                and attrs.get(attr) not in (None, "")
                and text_key(identity[field]) != text_key(attrs[attr])
                for field, attr in [("parallel_name", "parallel"), ("serial_total", "serial_total")]
            ):
                continue
            found.append(entity)
        return found

    def enrich(self, identity: dict, *, player_aliases=()) -> tuple[dict, list[dict]]:
        result = dict(identity)
        candidates = self.lookup(identity, player_aliases=player_aliases)
        changes = []
        # Checklist membership identifies a card kind, not the physical parallel.
        # Do not auto-fill autograph/print-run/parallel from a base-only checklist.
        if candidates and not (identity.get("insert_name") or identity.get("insert")):
            values = {row["attributes"].get("insert") for row in candidates}
            if len(values) == 1 and next(iter(values)):
                value = next(iter(values))
                result["insert_name"] = value
                attrs = candidates[0]["attributes"]
                changes.append(
                    {
                        "field": "insert_name",
                        "from": None,
                        "to": value,
                        "entity_id": str(candidates[0]["id"]),
                        "knowledge_version": self.version,
                        "source_url": attrs["source_url"],
                        "source_locator": attrs.get("source_locator"),
                        "method": "scoped_official_checklist",
                    }
                )
        # A single release-year label is not a conflicting full season. Expand
        # it only through a published, product-scoped mapping AND matching card
        # facts. This is a runtime interpretation, never a stored identity edit.
        seasons = {(row["attributes"].get("season_start"), row["attributes"].get("season_end")) for row in candidates}
        if (self.mappings_for(identity) and len(seasons) == 1
                and identity.get("season_end") in (None, identity.get("season_start"))):
            start, end = next(iter(seasons))
            if start and end and (identity.get("season_start"), identity.get("season_end")) != (start, end):
                result.update(season_start=start, season_end=end)
                changes.append({"field": "season", "from": [identity.get("season_start"), identity.get("season_end")],
                                "to": [start, end], "method": "scoped_official_checklist",
                                "knowledge_version": self.version, "source_url": candidates[0]["attributes"]["source_url"]})
        return result, changes
