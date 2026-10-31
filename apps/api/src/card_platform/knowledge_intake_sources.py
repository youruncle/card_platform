"""Bounded public-image acquisition. Every output remains an unreviewed candidate."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from card_platform.ingestion.cardhobby import CARDHOBBY_SEARCH_URL, CardHobbyProvider
from card_platform.ingestion.cardhobby_public_item import (
    canonical_public_item_url, parse_public_item_html, public_gallery_for_primary,
)
from card_platform.knowledge_media import validate_image

HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': 'https://www.cardhobby.com.cn/market/search?ckey=0&keyword=',
    'User-Agent': 'Mozilla/5.0 CardPlatformKnowledgeIntake/1.0',
}


def content_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), default=str).encode()).hexdigest()


def search_rows(payload):
    if not isinstance(payload, dict) or payload.get('result', 1) != 1:
        raise ValueError('search_response_unconfirmed')
    rows, counts = [], []

    def visit(value):
        if isinstance(value, dict):
            if 'ID' in value and 'Title' in value:
                rows.append(value)
            else:
                counts.extend(v for k, v in value.items()
                              if k in {'Total', 'TotalCount', 'PageIndex'} and v is not None)
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    if not rows and not counts:
        raise ValueError('search_response_unconfirmed_empty_shell')
    return list({str(row['ID']): row for row in rows}.values())


def _product_words(identity):
    return list(dict.fromkeys(word for word in re.findall(
        r"[\w'-]+", str(identity.get('product_name') or ''))
        if word.casefold() not in {'topps', 'panini', 'upper', 'deck', 'basketball'}))


def source_queries(identity):
    player = str(identity.get('player_name') or '').strip()
    number = str(identity.get('card_number') or '').strip()
    product = ' '.join(_product_words(identity))
    if not player or not number:
        raise ValueError('missing_source_query_identity')
    if not product:
        # Standalone source probes may lack product scope; real catalog tasks have it.
        return [f'{player} {number}']
    return list(dict.fromkeys(' '.join(str(part) for part in parts if part) for parts in [
        [player, identity.get('season_start'), product, identity.get('insert_name'), number],
        [player, product, identity.get('insert_name'), number],
    ]))


def rank_rows(rows, identity):
    """Prioritize catalog scope, never convert title similarity into identity proof."""
    words = [w.casefold() for w in _product_words(identity)]
    def score(row):
        title = str(row.get('Title') or '').casefold()
        value = 10 * sum(word in title for word in words)
        if words and all(word in title for word in words):
            value += 20
        if str(identity.get('season_start') or 'NO_YEAR') in title:
            value += 5
        for sibling in ['updates', 'sapphire', 'cosmic', 'university', 'wnba', 'nbl']:
            if sibling in title and sibling not in words:
                value -= 10
        return value
    return sorted(rows, key=score, reverse=True)


def allowed_url(url, kind):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port
                or parsed.fragment):
            return False
        if kind == 'image':
            return (parsed.netloc == 'images.cardhobby.com'
                    and bool(parsed.path.strip('/')) and not parsed.query)
        return url == CARDHOBBY_SEARCH_URL or canonical_public_item_url(url) == url
    except (ValueError, TypeError):
        return False


class CardHobbyImageSource:
    def __init__(self, output, *, client=None, delay=1.5, pages=1, max_items=2,
                 max_images=4, max_bytes=12 * 1024 * 1024):
        if (delay < 0 or not 1 <= pages <= 3 or not 1 <= max_items <= 10
                or not 1 <= max_images <= 8 or max_bytes <= 0):
            raise ValueError('invalid_acquisition_limits')
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=25, follow_redirects=False)
        self.owned_client = client is None
        self.delay, self.pages = delay, pages
        self.max_items, self.max_images, self.max_bytes = max_items, max_images, max_bytes
        self.last_request = 0.0
        self.request_count = 0

    def close(self):
        if self.owned_client:
            self.client.close()

    def fetch(self, url, *, kind, params=None):
        if not allowed_url(url, kind):
            raise ValueError('unapproved_source_url')
        time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.request_count += 1
        with self.client.stream('GET', url, params=params, headers=HEADERS,
                                follow_redirects=False) as response:
            if response.is_redirect:
                # A source/CDN redirect is a new candidate, never an arbitrary fetch proxy.
                raise ValueError('source_redirect_not_followed')
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_bytes:
                    raise ValueError('capture_size_limit')
                chunks.append(chunk)
            data = b''.join(chunks)
            digest = sha256(data).hexdigest()
            relative = f'captures/{digest}.bin'
            path = self.output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temporary = path.with_name(f'.{digest}.{uuid4().hex}.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            elif sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('existing_capture_checksum_mismatch')
            record = dict(path=relative, sha256=digest, bytes=size,
                        url=str(response.url), original_url=url,
                        status_code=response.status_code,
                        content_type=response.headers.get('content-type', ''),
                        captured_at=datetime.now(timezone.utc).isoformat(),
                        request=dict(headers=HEADERS, params=params))
            receipt_path = f"captures/receipt-{content_hash(record)}.json"
            (self.output / receipt_path).write_text(json.dumps(record, ensure_ascii=False, indent=2))
            return {**record, 'receipt_path': receipt_path}

    def capture(self, identity):
        initial_requests = self.request_count
        result = dict(status='no_results', attempts=[], candidates=[])
        rows, row_sources, exhausted = [], {}, set()
        for query, page in [(q, page) for q in source_queries(identity)
                            for page in range(1, self.pages + 1)]:
            if query in exhausted:
                continue
            params = CardHobbyProvider.search_params(query, page, identity=identity)
            attempt = dict(platform='cardhobby', query=query, page=page,
                           executed_at=datetime.now(timezone.utc).isoformat(),
                           request=dict(url=CARDHOBBY_SEARCH_URL, headers=HEADERS, params=params))
            result['attempts'].append(attempt)
            try:
                ref = self.fetch(CARDHOBBY_SEARCH_URL, kind='search', params=params)
                attempt['capture'] = ref
                if ref['status_code'] != 200:
                    result['status'] = 'blocked' if ref['status_code'] in {403, 429} else 'error'
                    attempt.update(outcome=result['status'], diagnostic=f"HTTP {ref['status_code']}")
                    break
                page_rows = search_rows(json.loads((self.output / ref['path']).read_bytes()))
                attempt.update(outcome='found' if page_rows else 'no_results', rows=len(page_rows))
                rows.extend(page_rows)
                row_sources.update({str(row['ID']): ref for row in page_rows})
                if len(rows) >= self.max_items:
                    break
                if not page_rows:
                    exhausted.add(query)
            except (ValueError, OSError, httpx.HTTPError) as error:
                attempt.update(outcome='error', diagnostic=str(error))
                result['status'] = 'error'
                break
        for row in rank_rows(list({str(r['ID']): r for r in rows}.values()), identity)[:self.max_items]:
            item_id = str(row['ID'])
            if not re.fullmatch(r'\d{1,40}', item_id):
                continue
            url = f'https://cardhobby.com.cn/market/item/{item_id}'
            attempt = dict(platform='cardhobby', query=query, item_id=item_id,
                           executed_at=datetime.now(timezone.utc).isoformat(),
                           request=dict(url=url, headers=HEADERS))
            result['attempts'].append(attempt)
            try:
                ref = self.fetch(url, kind='item')
                attempt['capture'] = ref
                if ref['status_code'] != 200:
                    raise ValueError(f"item_http_{ref['status_code']}")
                html = (self.output / ref['path']).read_bytes().decode('utf-8')
                parsed = parse_public_item_html(html, url)
                gallery = public_gallery_for_primary(parsed, url, row.get('TitImg'))
                if not gallery:
                    raise ValueError('item_gallery_unconfirmed')
                images, failures = [], []
                for image_url in gallery['image_urls'][:self.max_images]:
                    image_ref = None
                    try:
                        image_ref = self.fetch(image_url, kind='image')
                        if image_ref['status_code'] != 200:
                            raise ValueError(f"image_http_{image_ref['status_code']}")
                        info = validate_image((self.output / image_ref['path']).read_bytes())
                        images.append({**image_ref, **info, 'side': 'unknown'})
                    except (ValueError, OSError, httpx.HTTPError) as error:
                        failures.append(dict(url=image_url, capture=image_ref, diagnostic=str(error)))
                if not images:
                    attempt['image_failures'] = failures
                    raise ValueError('no_valid_images_acquired')
                candidate = dict(platform='cardhobby', source_authority='public_specimen_listing',
                                 source_url=url, item_id=item_id, title=row.get('Title'),
                                 status='pending_review', images=images, source_capture=ref,
                                 acquisition_failures=failures,
                                 source_gallery=gallery,
                                 search_capture=row_sources[item_id])
                candidate['content_sha256'] = content_hash(candidate)
                result['candidates'].append(candidate)
                attempt.update(outcome='partial' if failures else 'captured', images=len(images),
                               image_failures=failures)
            except (ValueError, OSError, UnicodeError, httpx.HTTPError) as error:
                attempt.update(outcome='error', diagnostic=str(error))
        if result['candidates']:
            result['status'] = ('error' if any(a.get('outcome') in {'partial', 'error', 'blocked'}
                                              for a in result['attempts']) else 'captured')
        elif rows:
            result['status'] = 'error'
        result['http_requests'] = self.request_count - initial_requests
        return result
