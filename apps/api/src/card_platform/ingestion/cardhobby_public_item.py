"""Public item discovery and deterministic closed-auction facts; no order data."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from card_platform.cardhobby_order_html import Document, Node
from card_platform.domain.listing_title import GRADING_DETAIL_PATTERN


def canonical_public_item_url(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.netloc.lower() not in {'cardhobby.com.cn', 'www.cardhobby.com.cn'}
                or url.username or url.password or url.port or url.query or url.fragment):
            return None
        match = re.fullmatch(r'/market/item/(\d{1,40})/?', url.path, re.I)
    except ValueError:
        return None
    return f'https://cardhobby.com.cn/market/item/{match[1]}' if match else None


def public_item_urls_from_notes(notes: object, *, limit: int = 2) -> list[str]:
    urls = []
    for value in re.findall(r'https://[^\s<>"\'()，。；）]+', str(notes or ''), re.I):
        canonical = canonical_public_item_url(value)
        if canonical and canonical not in urls:
            urls.append(canonical)
        if len(urls) >= limit:
            break
    return urls


def _image_url(value):
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.netloc == 'images.cardhobby.com' and not url.username:
            return urlunsplit((url.scheme, url.netloc, url.path, '', ''))
    except ValueError:
        pass
    return None


def public_gallery_for_primary(payload: dict, source_url: str, primary_image_url: str | None) -> dict:
    """Associate page images with an independently obtained sale, never its price.

    A rejected HTML sale can still document a gallery. Bind only the exact item
    and a primary image actually present in that gallery; image order is not a
    front/back label, and the page supplies no finish or condition authority.
    """
    canonical = canonical_public_item_url(source_url)
    metadata = ((payload.get('sale') or {}).get('source_metadata')
                or payload.get('source_metadata') or {})
    if (not canonical or metadata.get('kind') != 'public_item_html'
            or canonical_public_item_url(metadata.get('source_url')) != canonical
            or 'public_item_id_mismatch' in payload.get('reasons', [])):
        return {}
    try:
        primary = urlsplit(primary_image_url)
        allowed_transform = not primary.query or bool(re.fullmatch(
            r'imageView2/\d+(?:/[a-z]+/\d+)*=?', unquote(primary.query)))
        if (primary.scheme != 'https' or primary.netloc != 'images.cardhobby.com'
                or primary.fragment or not allowed_transform or not primary.path.strip('/')):
            return {}
        captured = datetime.fromisoformat(metadata['retrieved_at'])
        images = metadata['image_urls']
        if (captured.tzinfo is None or captured > datetime.now(timezone.utc)
                or not re.fullmatch(r'[0-9a-f]{64}', metadata['raw_html_sha256'])
                or not isinstance(images, list) or not 1 <= len(images) <= 20
                or any(not isinstance(image, str) or _image_url(image) != image for image in images)
                or _image_url(primary_image_url) not in images):
            return {}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    return {'contract': 'public-source-gallery-v1', 'source_url': canonical,
            'primary_image_url': primary_image_url, 'image_urls': list(dict.fromkeys(images)),
            'retrieved_at': metadata['retrieved_at'], 'raw_html_sha256': metadata['raw_html_sha256'],
            'discovery_only': True}


def _visible(node):
    while node:
        if ('hidden' in node.attrs or {'hide', 'hidden'} & set(node.attrs.get('class', '').split())
                or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', node.attrs.get('style', ''), re.I)):
            return False
        node = node.parent
    return True


def _primary_price_currency(price_node):
    # Currency must label the primary price itself, never shipping or the USD estimate.
    cell = price_node.closest('td') or price_node.parent
    def visible_text(node):
        if not _visible(node) or node.attrs.get('id') == 'usdPrice':
            return ''
        return ' '.join(visible_text(child) if isinstance(child, Node) else child
                        for child in node.children)
    text = visible_text(cell) if cell else ''
    if re.search(r'[$€£]|\b(?:USD|JPY|EUR|GBP|HKD|AUD|CAD)\b|日元|港币|美元', text, re.I):
        return None
    return 'CNY' if re.search(r'[￥¥]|\bCNY\b|人民币', text, re.I) else None


def parse_public_item_html(html: str, source_url: str, *, now: datetime | None = None) -> dict:
    url = canonical_public_item_url(source_url)
    if not url:
        raise ValueError('unapproved public item URL')
    now = now or datetime.now(timezone.utc)
    doc = Document(html)
    reasons = []
    item_id = url.rsplit('/', 1)[-1]
    titles = [node for node in doc.all() if 'newcontitle' in node.attrs.get('class', '').split()]
    title = ' '.join(child.strip() for child in titles[0].children if isinstance(child, str) and child.strip()) if len(titles) == 1 else ''
    if not title:
        reasons.append('public_item_title_missing')
    sold = [node for node in doc.all('img') if 'soldMark' in node.attrs.get('class', '').split()
            and node.attrs.get('src', '').lower() == '/content/img/sold.png' and _visible(node)]
    if len(sold) != 1:
        reasons.append('public_item_not_explicitly_sold')
    if (re.search(r'已撤拍|拍卖已取消|商品已下架|一口价', title)
            or any(node.text() in {'已撤拍', '拍卖已取消', '商品已下架', '已取消', '一口价'} for node in doc.all())
            or doc.by_id('buyoutprice')):
        reasons.append('public_item_cancelled_or_fixed_price')
    end_text = None
    for row in doc.all('tr'):
        cells = [child for child in row.children if isinstance(child, Node) and child.tag == 'td']
        if cells and cells[0].text() == '结束时间':
            end_text = cells[-1].text()
            break
    try:
        ended = datetime.strptime(end_text or '', '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        if ended > now:
            raise ValueError('future auction')
    except ValueError:
        ended = None
        reasons.append('public_item_not_past_ended_auction')
    bids = [node for node in doc.all() if node.attrs.get('id') == 'bidcount']
    bid_count = int(bids[0].text()) if len(bids) == 1 and bids[0].text().isdigit() else 0
    if bid_count <= 0:
        reasons.append('public_item_no_positive_bid')
    bid_link = bids[0].closest('a') if len(bids) == 1 else None
    if bid_link is None or not re.fullmatch(rf'/market/bidrecords/{re.escape(item_id)}/?', bid_link.attrs.get('href', ''), re.I):
        reasons.append('public_item_id_mismatch')
    prices = [node for node in doc.all() if node.attrs.get('id') == 'currentPrice']
    price = None
    currency = None
    if len(prices) != 1 or not _visible(prices[0]):
        reasons.append('public_item_closing_price_missing')
    else:
        currency = _primary_price_currency(prices[0])
        if currency is None:
            reasons.append('public_item_closing_currency_unverified')
        raw = prices[0].text()
        try:
            if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?', raw):
                raise InvalidOperation
            price = Decimal(raw.replace(',', ''))
            if not price.is_finite() or price <= 0:
                raise InvalidOperation
        except InvalidOperation:
            reasons.append('public_item_closing_price_invalid')
    images = []
    gallery = doc.by_id('layer-photos-demo')
    for node in gallery.all('img') if gallery else ():
        value = _image_url(node.attrs.get('layer-src') or node.attrs.get('bimg') or node.attrs.get('src', ''))
        if value and value not in images:
            images.append(value)
    if not images:
        # Older single-image listings have an empty thumbnail list but a real
        # product preview. Never fall back to arbitrary page images (avatars).
        previews = [node for node in doc.all() if node.attrs.get('id') == 'preview']
        if len(previews) == 1 and _visible(previews[0]):
            primary = [node for node in previews[0].all('img') if _visible(node)]
            if len(primary) == 1:
                value = _image_url(primary[0].attrs.get('jqimg') or primary[0].attrs.get('src', ''))
                if value:
                    images.append(value)
    if not images:
        reasons.append('public_item_images_missing')
    # This is the explicitly labeled domestic shipping charge, not order total.
    shipping = '0'
    shipping_verified = False
    for node in doc.all():
        if 'regtitle' in node.attrs.get('class', '').split() and '中国默认地区' in node.text():
            match = re.search(r'[￥¥]\s*(\d+(?:\.\d{1,2})?)', node.text())
            if match:
                shipping, shipping_verified = match[1], True
                break
    if not shipping_verified:
        reasons.append('public_item_shipping_not_verified')
    metadata = {'kind': 'public_item_html', 'retrieved_at': now.isoformat(),
                'raw_html_sha256': hashlib.sha256(html.encode()).hexdigest(),
                'source_url': url, 'payment_confirmed': False, 'image_urls': images,
                'shipping_verified': shipping_verified, 'currency': currency}
    if reasons:
        return {'sale': None, 'reasons': reasons, 'source_metadata': metadata}
    sale = {'item_id': item_id, 'source_url': url, 'title': title,
            'occurred_at': ended.isoformat(), 'sale_price': str(price), 'shipping_price': shipping,
            'bid_count': bid_count, 'sale_type': 'auction', 'evidence_level': 'ended_auction',
            'sold_status_evidence': f'公开商品页soldMark、过去结束时间、同商品{bid_count}次出价及CNY结束价；未确认付款',
            'image_url': images[0], 'match_confidence': '0.5',
            'matching_fields': ['cardhobby_public_item_ended_with_bids']}
    grade = GRADING_DETAIL_PATTERN.search(title)
    sale['grade_company'] = grade.group(1).upper() if grade else None
    sale['grade_value'] = grade.group(2) if grade else None
    metadata['content_binding_sha256'] = hashlib.sha256(json.dumps(
        {**sale, 'image_urls': images}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    sale['source_metadata'] = metadata
    return {'sale': sale, 'reasons': []}
