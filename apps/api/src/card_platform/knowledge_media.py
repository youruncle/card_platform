"""Auditable public card-reference acquisition; never writes business or knowledge tables.

PDF images require an explicit per-image content review. Their aspect ratio alone
cannot distinguish packaging, logos, and actual card designs. Unknown identities,
versions, and sides remain unknown. Media are samples, never whole-set coverage.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

PARSER_VERSION = 'official-media-v1'
_NON_CARD = re.compile(r'\b(logo|icon|banner|packaging|box|boxes|packs?|cases?|placeholder)\b', re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _normal(value: str) -> str:
    value = unicodedata.normalize('NFKD', unescape(value)).lower()
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def product_binding(product: dict, text: str) -> bool:
    """Require season, sport, and product words; reject common sibling editions."""
    normalized = _normal(text)
    title = _normal(product.get('canonical_title') or product['title'])
    start = str(product['season_start'])
    if start not in normalized or 'basketball' not in normalized:
        return False
    end = product.get('season_end')
    if end and end != product['season_start']:
        if not re.search(rf'{start}\s+(?:{end}|{str(end)[-2:]})\b', normalized):
            return False
    words = [w for w in title.split() if not w.isdigit() and w not in {
        'panini', 'topps', 'basketball', 'hobby', 'retail', 'edition', 'america'}]
    if not all(re.search(rf'\b{re.escape(w)}\b', normalized) for w in words):
        return False
    # An edition in the product heading is significant. A Sapphire parallel
    # caption elsewhere in a Court Kings sell sheet is not a Sapphire product.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = normalized
    for index in range(len(lines)):
        window = _normal(' '.join(lines[index:index + 3]))
        if start in window and 'basketball' in window and all(
            re.search(rf'\b{re.escape(w)}\b', window) for w in words
        ):
            heading = window
            break
    for sibling in ('sapphire', 'wnba', 'euroleague', 'university', 'nbl', 'updates', 'cosmic'):
        if sibling in heading and sibling not in title:
            return False
    return True


def _side(text: str) -> str:
    text = unquote(text)
    if re.search(r'(?:\b|_)(?:BK|BACK)(?:\b|_)', text, re.I):
        return 'back'
    if re.search(r'(?:\b|_)(?:FR|FRONT)(?:\b|_)', text, re.I):
        return 'front'
    return 'unknown'


class _Images(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.candidates: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in {'img', 'source'}:
            return
        caption = attrs.get('alt', '') or attrs.get('title', '')
        image_url = attrs.get('data-src') or attrs.get('data-lazy-src') or attrs.get('src')
        srcset = attrs.get('data-srcset') or attrs.get('srcset')
        if srcset:
            choices = []
            for item in srcset.split(','):
                parts = item.strip().split()
                if parts:
                    size = float(re.sub('[^0-9.]', '', parts[-1]) or 0) if len(parts) > 1 else 0
                    choices.append((size, parts[0]))
            if choices:
                image_url = max(choices)[1]
        if not image_url or image_url.startswith('data:'):
            return
        image_url = urljoin(self.base_url, unescape(image_url))
        filename = unquote(urlparse(image_url).path.rsplit('/', 1)[-1]).replace('_', ' ')
        if _NON_CARD.search(filename + ' ' + caption):
            return
        side = _side(image_url + ' ' + caption)
        self.candidates.append({'image_url': image_url, 'caption': caption,
                                'side': side, 'image_type': 'unknown'})


def extract_media_candidates(html: str, page_url: str) -> list[dict]:
    parser = _Images(page_url)
    parser.feed(html)
    return list({item['image_url']: item for item in parser.candidates}.values())


def validate_image(data: bytes, *, max_pixels: int = 60_000_000) -> dict:
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if min(width, height) < 80 or width * height > max_pixels:
                raise ValueError('invalid_image_dimensions')
            image.verify()
            return {'width': width, 'height': height, 'format': image.format}
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('invalid_image_payload') from exc


def fetch_capture(url: str, output: Path, *, client: httpx.Client,
                  max_bytes: int = 30 * 1024 * 1024, retry_failed: bool = False) -> dict:
    """Content-addressed immutable bytes plus URL record, including HTTP failures."""
    output.mkdir(parents=True, exist_ok=True)
    record_path = output / f'url-{_sha(url.encode())}.json'
    if record_path.exists():
        record = json.loads(record_path.read_text())
        path = Path(record['path'])
        if path.exists() and _sha(path.read_bytes()) == record['sha256']:
            if record['status_code'] == 200 or not retry_failed:
                return record
    if urlparse(url).scheme not in {'http', 'https'}:
        raise ValueError('unsupported_url_scheme')
    with client.stream('GET', url) as response:
        if int(response.headers.get('content-length') or 0) > max_bytes:
            raise ValueError('size_limit')
        pieces, length = [], 0
        for piece in response.iter_bytes():
            length += len(piece)
            if length > max_bytes:
                raise ValueError('size_limit')
            pieces.append(piece)
        data = b''.join(pieces)
        digest = _sha(data)
        content_type = response.headers.get('content-type', '')
        extension = '.pdf' if data.startswith(b'%PDF') else '.html' if 'html' in content_type else '.bin'
        path = output / (digest + extension)
        if not path.exists() or _sha(path.read_bytes()) != digest:
            path.write_bytes(data)
        record = {'url': url, 'final_url': str(response.url), 'status_code': response.status_code,
                  'content_type': content_type, 'path': str(path.resolve()), 'sha256': digest,
                  'bytes': length, 'captured_at': _now()}
    _atomic_json(record_path, record)
    return record


def extract_pdf_images(data: bytes, output: Path) -> list[dict]:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    if len(reader.pages) > 100:
        raise ValueError('pdf_page_limit')
    output.mkdir(parents=True, exist_ok=True)
    candidates = []
    for page_number, page in enumerate(reader.pages, 1):
        for index, item in enumerate(page.images):
            try:
                info = validate_image(item.data)
            except ValueError:
                continue
            digest = _sha(item.data)
            path = output / f'{digest}{Path(item.name).suffix}'
            if not path.exists() or _sha(path.read_bytes()) != digest:
                path.write_bytes(item.data)
            candidates.append({**info, 'path': str(path.resolve()), 'sha256': digest,
                               'page': page_number, 'image_index': index,
                               'locator': f'pdf:page={page_number};image={index};name={item.name}',
                               'side': 'unknown', 'status': 'unverified_candidate'})
    return candidates


def collect_media(products: list[dict], pages: list[dict], output: Path, *,
                  client: httpx.Client | None = None, delay: float = 0.0,
                  retry_failed: bool = False) -> dict:
    """Collect selected original media and produce an honest product-gap report.

    pages is an acquisition queue with source_key/url and optional approved_images
    keyed by '<one-based page>:<zero-based image>'. Review records contain role,
    caption, side, image_type, basis. Source labels are verified against the PDF
    text, or captured HTML title; binding_text is an additional captured-page
    locator and is never sufficient when the downloaded source contradicts it.
    """
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    owned_client = client is None
    client = client or httpx.Client(timeout=45, follow_redirects=True,
                                   headers={'User-Agent': 'CardPlatformReferenceResearch/1.0'})
    by_product = {p['source_key']: p for p in products}
    assets, reports, candidate_rows = [], {}, []
    try:
        for key, product in by_product.items():
            reports[key] = {'source_key': key, 'title': product['title'],
                            'publisher': product['publisher'], 'season_start': product['season_start'],
                            'status': 'no_image_source_discovered', 'sources': [], 'asset_count': 0}
        for source in pages:
            key = source['source_key']
            if key not in by_product:
                continue
            product, report = by_product[key], reports[key]
            result = {'url': source['url'], 'status': 'pending'}
            report['sources'].append(result)
            try:
                capture = fetch_capture(source['url'], output / 'sources', client=client,
                                        retry_failed=retry_failed)
                result.update(capture)
                if capture['status_code'] != 200:
                    result['status'] = f"http_{capture['status_code']}"
                    continue
                data = Path(capture['path']).read_bytes()
                if data.startswith(b'%PDF'):
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(data))
                    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                    # Image-only PDFs can be bound by separately captured official
                    # product pages, but that exception must be explicit and retained.
                    binding = text
                    if not binding.strip() and source.get('binding_artifact_path'):
                        companion = Path(source['binding_artifact_path']).read_bytes()
                        host = urlparse(source.get('binding_artifact_url', '')).hostname or ''
                        official_host = any(host == domain or host.endswith('.' + domain)
                                            for domain in ('topps.com', 'paniniamerica.net'))
                        companion_text = companion.decode('utf-8', errors='replace')
                        if (official_host and _sha(companion) == source.get('binding_artifact_sha256')
                                and source['url'] in unescape(companion_text)):
                            binding = companion_text
                    visual = source.get('visual_binding', {})
                    if not product_binding(product, binding) and visual.get('render_path'):
                        rendered = Path(visual['render_path']).read_bytes()
                        # A raster heading can be transcribed after actual visual
                        # review, pinned to both PDF bytes and its page rendering.
                        # Contradicting explicit seasons in the PDF are not waived.
                        seasons = re.findall(r'\b(20\d{2})\s*[-/–]\s*(\d{2,4})\b', text)
                        expected = str(product['season_start'])
                        contradiction = bool(seasons) and not any(y == expected for y, _ in seasons)
                        if (not contradiction and visual.get('basis') and visual.get('page')
                                and visual.get('pdf_sha256') == capture['sha256']
                                and visual.get('render_sha256') == _sha(rendered)):
                            validate_image(rendered)
                            binding = visual.get('text', '')
                    if not product_binding(product, binding):
                        result['status'] = 'product_binding_unverified'
                        result['extracted_text_excerpt'] = text[:1000]
                        continue
                    result['product_binding_excerpt'] = binding[:2000]
                    candidates = extract_pdf_images(data, output / 'originals')
                    if source.get('approved_images') and source.get('approved_source_sha256') != capture['sha256']:
                        result['status'] = 'review_source_hash_mismatch'
                        continue
                    for candidate in candidates:
                        review_key = f"{candidate['page']}:{candidate['image_index']}"
                        review = source.get('approved_images', {}).get(review_key)
                        candidate_rows.append({**candidate, 'source_key': key, 'source_url': source['url'],
                                               'review': review})
                        if not review or review.get('role') != 'card_reference' or not review.get('basis'):
                            continue
                        assets.append(_asset(product, source, capture, candidate, review))
                    result['candidate_count'] = len(candidates)
                    result['status'] = 'processed'
                else:
                    html = data.decode('utf-8', errors='replace')
                    title = re.search(r'<(?:h1|title)[^>]*>(.*?)</(?:h1|title)>', html, re.S | re.I)
                    if not title or not product_binding(product, re.sub('<[^>]+>', ' ', title[1])):
                        result['status'] = 'product_binding_unverified'
                        continue
                    candidates = extract_media_candidates(html, source['url'])
                    for candidate in candidates:
                        image_capture = fetch_capture(candidate['image_url'], output / 'sources',
                                                      client=client, max_bytes=15 * 1024 * 1024,
                                                      retry_failed=retry_failed)
                        if image_capture['status_code'] != 200:
                            continue
                        info = validate_image(Path(image_capture['path']).read_bytes())
                        review = source.get('approved_images', {}).get(candidate['image_url'])
                        if not review or review.get('role') != 'card_reference':
                            candidate_rows.append({**candidate, **image_capture, 'source_key': key,
                                                   'status': 'unverified_candidate'})
                            continue
                        assets.append(_asset(product, source, capture,
                                             {**candidate, **image_capture, **info,
                                              'locator': 'html:img:' + candidate['image_url']}, review))
                    result['candidate_count'] = len(candidates)
                    result['status'] = 'processed'
            except (httpx.HTTPError, ValueError, OSError, KeyError) as exc:
                result['status'] = 'source_error'
                result['error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
            finally:
                if delay:
                    time.sleep(delay)
        # Same original image is one asset per product, even if PDF/gallery repeats it.
        assets = list({(a['source_key'], a['sha256']): a for a in assets}.values())
        for asset in assets:
            reports[asset['source_key']]['asset_count'] += 1
        for report in reports.values():
            if report['asset_count']:
                report['status'] = 'image_partial'
            elif report['sources']:
                report['status'] = 'image_unavailable' if any(
                    r['status'] != 'processed' for r in report['sources']) else 'no_verified_card_images'
        asset_path = output / 'assets.jsonl'
        temporary = asset_path.with_suffix('.jsonl.tmp')
        temporary.write_text(''.join(json.dumps(a, ensure_ascii=False) + '\n' for a in assets))
        temporary.replace(asset_path)
        _atomic_json(output / 'candidates.json', candidate_rows)
        summary = {'contract': PARSER_VERSION, 'captured_at': _now(), 'asset_count': len(assets),
                   'product_count': len(products), 'products_with_images': sum(
                       r['asset_count'] > 0 for r in reports.values()),
                   'discovery_complete': False, 'products': list(reports.values()),
                   'limits': ['Official design samples do not imply complete card/parallel coverage.',
                              'Card identity and version remain null unless individually evidenced.',
                              'No front/back pair is inferred.',
                              'Public source access does not confer redistribution permission.']}
        _atomic_json(output / 'report.json', summary)
        return summary
    finally:
        if owned_client:
            client.close()


def _asset(product: dict, source: dict, capture: dict, candidate: dict, review: dict) -> dict:
    digest = candidate['sha256']
    return {'asset_id': 'knowledge-media-' + _sha((product['source_key'] + ':' + digest).encode())[:24],
            'path': candidate['path'], 'sha256': digest, 'source_url': source['url'],
            'image_url': candidate.get('image_url', source['url'] + '#' + candidate['locator']),
            'source_key': product['source_key'], 'title': product['title'],
            'publisher': product['publisher'], 'season_start': product['season_start'],
            'season_end': product.get('season_end'), 'side': review.get('side', 'unknown'),
            'image_type': review.get('image_type', 'unknown'), 'caption': review.get('caption', ''),
            'status': 'ready', 'captured_at': capture['captured_at'],
            'width': candidate['width'], 'height': candidate['height'], 'format': candidate['format'],
            'source_artifact_path': capture['path'], 'source_artifact_sha256': capture['sha256'],
            'source_authority': source.get('source_authority', 'official_host'),
            'locator': candidate['locator'], 'review_basis': review.get('basis'),
            'reviewed_at': review.get('reviewed_at', _now()), 'parser_version': PARSER_VERSION,
            'review_artifact_path': review.get('review_artifact_path'),
            'product_binding_evidence': source.get('visual_binding') or source.get('binding_evidence'),
            'card_number': None, 'player': None, 'variant': None, 'pair_asset_id': None,
            'visibility': 'public_reference', 'usage_scope': 'internal_reference_research',
            'rights': 'Original source rights retained; public access is not a redistribution license.'}
