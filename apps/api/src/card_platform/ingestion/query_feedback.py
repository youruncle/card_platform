"""Finite, query-only alias feedback. No identity or matching decisions."""
import re
import unicodedata
from collections import defaultdict


def discovery_subject(value):
    """Fold Latin accents for seller search, preserving Japanese and other scripts."""
    return ''.join(
        ''.join(c for c in unicodedata.normalize('NFD', char) if not unicodedata.combining(c))
        if 'LATIN' in unicodedata.name(char, '') else char
        for char in str(value or '').strip()
    )


def short_insert_query_term(value):
    """Remove English label wrappers for discovery, never identity matching."""
    text = ' '.join(str(value or '').split())
    match = re.fullmatch(r'(.+?)\s+cards?', text, re.I)
    if not match:
        return None
    core = re.sub(r'^original\s+', '', match[1], flags=re.I).strip()
    if core.casefold() in {'original', 'card', 'cards', 'trading', 'collectible'}:
        return None
    return core if re.search(r'[A-Za-z]{3}', core) else None


class BoundedAliasFeedback:
    CRITICAL = ('subject_print_run', 'collector_prefix', 'short_insert', 'refine_print_run', 'brand_release_product', 'player_release_recovery', 'refine_product',
        'product_alias_rotation', 'product_spelling_rotation', 'season_insert',
        'release_parallel', 'explicit_base', 'player_alias_rotation')

    @staticmethod
    def key(value):
        return ''.join(char for char in discovery_subject(value).casefold() if char.isalnum())

    @staticmethod
    def query_key(value):
        return ' '.join(str(value).casefold().split())

    def __init__(self, plan, *, canonical, trusted_aliases, recovery=(), seeds=(), limit=12, enabled=True):
        self.canonical = str(canonical or '').strip()
        self.canonical_key = self.key(self.canonical)
        self.enabled = enabled and bool(self.canonical)
        self.limit = min(12, max(0, limit))
        self.names = sorted(set([self.canonical, *(str(x).strip() for x in trusted_aliases if x)]), key=len, reverse=True)
        self.zeros = defaultdict(int)
        self.yield_by_alias = defaultdict(int)
        self.seen_raw = set()
        self.seen_candidates = set()
        self.issued = set()
        self.covered = set()
        self.bootstrap = [('public_item_source', url, 1) for url in seeds][:2]
        # Official work names are not person aliases. Issue/year relaxation
        # must survive alias bootstrapping and share the same total request cap.
        self.bootstrap.extend((stage, query, 1) for stage, query in plan
                              if stage in {'official_work_title', 'official_work_release'})
        self.pending = []
        allowed = {self.canonical_key}
        primary = next((self._match(q) for _, q in plan
                        if self._match(q) and self.key(self._match(q)[0]) != self.canonical_key), None)
        self.enabled = (self.enabled and primary is not None
                        and any(self._match(q) and self._match(q)[1].strip() for _, q in plan))
        if primary:
            allowed.add(self.key(primary[0]))
        third = next(((stage, q) for stage, q in plan if stage == 'player_alias_rotation'
                      and self._match(q) and self.key(self._match(q)[0]) not in allowed), None)
        if third:
            allowed.add(self.key(self._match(third[1])[0]))
        self.allowed = allowed
        if self.enabled:
            anchors = []
            for stage, q in plan:
                if stage == 'strong_anchor' and self._match(q):
                    suffix = self._match(q)[1]
                    if self.query_key(suffix) not in {self.query_key(x) for x in anchors}:
                        anchors.append(suffix)
            for suffix in anchors[:2]:
                self.bootstrap.append(('strong_anchor', self._join(self.canonical, suffix), 1))
                if primary:
                    self.bootstrap.append(('strong_anchor', self._join(primary[0], suffix), 1))
            short_insert = next((self._match(q)[1] for stage, q in plan
                                 if stage == 'short_insert' and self._match(q)), None)
            if short_insert:
                self.bootstrap.append(('short_insert', self._join(self.canonical, short_insert), 1))
            product = next(((stage, q) for stage, q in plan if stage == 'refine_product' and self._match(q)), None)
            if product:
                self.bootstrap.append((product[0], self._join(self.canonical, self._match(product[1])[1]), 1))
            # Seller titles frequently omit the collector number. Sample the
            # canonical product-only query early so a cold translated alias
            # can yield to measured evidence before exhausting this same cap.
            relaxed_product = next(((stage, q) for stage, q in plan
                                    if stage == 'relax_product' and self._match(q)), None)
            if relaxed_product:
                self.bootstrap.append((relaxed_product[0], self._join(
                    self.canonical, self._match(relaxed_product[1])[1]), 1))
            if third:
                self.bootstrap.append((*third, 1))
        for stage, query in [*plan, *(recovery if self.enabled else ())][:36]:
            matched = self._match(query)
            if self.enabled and matched and self.key(matched[0]) not in allowed:
                query = self._join(self.canonical, matched[1])
            self.pending.append((stage, query, 1))

    @staticmethod
    def _join(name, suffix):
        return f'{name} {suffix}'.strip()

    def _match(self, query):
        for name in self.names:
            if not name:
                continue
            match = re.match(r'^' + re.escape(name) + r'(?:\s+|$)', str(query), re.I)
            if match:
                return name, str(query)[match.end():]
        return None

    def append(self, item):
        # Provider's existing continuation pages remain behind independent lanes.
        if len(self.pending) < 48:
            self.pending.append(item)

    def zero_count(self, alias):
        return self.zeros[self.key(alias)]

    def observe(self, query, *, raw_ids, candidate_ids, raw_count, errored=False):
        new_raw = set(raw_ids) - self.seen_raw
        new_candidates = set(candidate_ids) - self.seen_candidates
        self.seen_raw.update(raw_ids)
        self.seen_candidates.update(candidate_ids)
        match = self._match(query)
        if match and not errored:
            key = self.key(match[0])
            if raw_count == 0:
                self.zeros[key] += 1
            self.yield_by_alias[key] += len(new_candidates)
        return len(new_raw), len(new_candidates)

    def __iter__(self):
        while (self.bootstrap or self.pending) and len(self.issued) < self.limit:
            if self.bootstrap:
                stage, query, page = self.bootstrap.pop(0)
            else:
                priorities = [(self.CRITICAL.index(s), i) for i, (s, _, p) in enumerate(self.pending)
                              if self.enabled and p == 1 and s in self.CRITICAL and s not in self.covered]
                index = min(priorities, default=(0, 0))[1]
                stage, query, page = self.pending.pop(index)
            matched = self._match(query)
            if (self.enabled and page == 1 and matched and self.key(matched[0]) != self.canonical_key
                    and self.zeros[self.key(matched[0])] >= 2
                    and self.yield_by_alias[self.canonical_key] > 0):
                query = self._join(self.canonical, matched[1])
            key = (self.query_key(query), page)
            self.covered.add(stage)
            if key in self.issued:
                continue
            self.issued.add(key)
            yield stage, query, page
