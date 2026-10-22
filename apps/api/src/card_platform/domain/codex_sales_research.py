"""Admission for externally researched Codex sales, separate from formal providers.

Validation cannot perform browsing or attest that a human/model told the truth.
It requires auditable captures and fails closed on missing/contradictory facts.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from urllib.parse import urlparse

CONTRACT_VERSION = 'codex-sales-research-1'
MARKETS = ('ebay', 'cardhobby')
IDENTITY_FIELDS = ('player_name', 'brand_name', 'product_name', 'season_start', 'season_end',
                   'card_number', 'insert_name', 'parallel_name', 'serial_total',
                   'grade_company', 'grade_value', 'is_autograph', 'is_memorabilia')
REQUIRED_IDENTITY_ROLES = ('brand_name', 'product_name', 'season_start', 'card_number',
                           'parallel_name', 'grade_company', 'grade_value',
                           'is_autograph', 'is_memorabilia')
RESEARCH_INSTRUCTIONS = '''逐卡从当前正背原图识别身份，绑定collection_item_id与两面SHA256。
主动执行eBay历史成交搜索及卡淘历史成交搜索，各保存query、executed_at、outcome和capture_ref。
搜索缓存、0 query、旧分析、搜索计划均不等于本次检索。403/超时/无权限须保存阶段和原始诊断。
逐条打开商品与卡面，记录身份、产品/赛季/子集/卡号、颜色/纹理/平行、总编、评级和品相，
实际结束/成交日、实际金额、原币种、运费、卖家、同实卡/完整流水/寄售代码/再上检查。
挂牌、未知BestOffer、撤架、lot、异平行、异评级及不可核图聚合不能作为同款成交。
分列两市场，再综合证据充分度。单市场阻塞不伪造另一市场支持；运费另列，跨币种要有日期和来源的汇率。
证据不足价格null，不用经验、采购成本或旧系统价补置信。卡淘公开落槌不等于已付款。
将结果保存为codex-sales-research-1 JSON并运行scripts/codex_sales_research.py validate。
验证通过只证明结构与资格门通过，不能宣称全库准确率；写回业务另需明确范围授权。'''


def _date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def _amount(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _market_url(platform, value):
    parsed = urlparse(str(value or ''))
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return False
    if platform == 'cardhobby':
        return (host in {'cardhobby.com.cn', 'www.cardhobby.com.cn'}
                and bool(re.fullmatch(r'/market/item/\d+/?', parsed.path, re.I)))
    return platform == 'ebay' and bool(re.fullmatch(
        r'(?:www\.)?ebay\.(?:com|ca|co\.uk|com\.au|de|fr)', host)) and bool(
            re.search(r'/itm/(?:[^/]+/)?\d+', parsed.path))


def _same(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return re.sub(r'\s+', ' ', str(a).strip().casefold()) == re.sub(
        r'\s+', ' ', str(b).strip().casefold())


def _serial(values):
    number, total = (_amount(v) for v in values)
    if (number is not None and total is not None and 0 < number <= total
            and number == int(number) and total == int(total)):
        return int(number), int(total)
    return None


def _item_key(platform, url):
    path = urlparse(str(url or '')).path
    if platform == 'cardhobby':
        match = re.search(r'/market/item/(\d+)', path, re.I)
    else:
        match = re.search(r'/itm/(?:[^/]+/)?(\d+)', path, re.I)
    return str(platform), match[1] if match else str(url)


def research_backed_prices(snapshot, *, now=None):
    raw = snapshot.get('raw_result') or {}
    research = raw.get('market_research') or {}
    if raw.get('research_captures_verified') is not True:
        return False
    result = assess_sales_research(research, now=now)
    return (result['status'] == 'supported_observation'
        and snapshot.get('currency') == result['currency']
        and all(_amount(snapshot.get(k)) == _amount(result.get(v)) for k, v in [
            ('fair_value_low', 'low_value'), ('fair_value', 'fair_value'),
            ('fair_value_high', 'high_value')]))


def research_target_binding_reasons(data, item, image_hashes):
    """Bind the research itself, rather than only its enclosing snapshot."""
    reasons = []
    if not data.get('collection_item_id') or str(data['collection_item_id']) != str(item['id']):
        reasons.append('research_item_binding_mismatch')
    for side in ('front', 'back'):
        if not image_hashes.get(side) or data.get(f'{side}_sha256') != image_hashes[side]:
            reasons.append(f'{side}_research_image_binding_mismatch')
    identity = data.get('identity') or {}
    for field in IDENTITY_FIELDS:
        if not _same(identity.get(field), item.get(field)):
            reasons.append('research_target_identity_mismatch:' + field)
    # The machine serial identifies the owned physical copy excluded by research.
    # It must not be substituted while keeping the same edition/print run.
    if (identity.get('serial_number') is not None or item.get('serial_number') is not None):
        owned = _serial((item.get('serial_number'), item.get('serial_total')))
        if owned is None or owned != _serial((identity.get('serial_number'), identity.get('serial_total'))):
            reasons.append('research_owned_serial_binding_mismatch')
    return reasons


def assess_sales_research(data: dict, *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = dict(contract_version=CONTRACT_VERSION, status='research_incomplete',
        markets={}, accepted=[], rejected=[], reasons=[], currency=data.get('currency'),
        low_value=None, fair_value=None, high_value=None, confidence=0)
    if data.get('contract_version') != CONTRACT_VERSION:
        result['reasons'].append('missing_current_research_contract')
        return result
    target = data.get('identity') or {}
    if not all(re.fullmatch(r'[0-9a-f]{64}', str(data.get(k) or ''))
               for k in ('front_sha256', 'back_sha256')):
        result['reasons'].append('target_images_unbound')
    from card_platform.domain.card_identity import is_placeholder_identity_value
    if (any(k not in target for k in REQUIRED_IDENTITY_ROLES)
            or not all(target.get(k) for k in ('brand_name', 'product_name', 'season_start', 'card_number', 'parallel_name'))
            or not all(isinstance(target.get(k), bool) for k in ('is_autograph', 'is_memorabilia'))
            or not (target.get('player_name') or target.get('insert_name'))
            or any(is_placeholder_identity_value(target.get(k)) for k in
                   ('player_name', 'product_name') if target.get(k))):
        result['reasons'].append('target_identity_unresolved')
    for platform in MARKETS:
        searches = [s for s in data.get('searches', []) if isinstance(s, dict)
                    and s.get('platform') == platform]
        valid = [s for s in searches if s.get('query') and s.get('capture_ref')
                 and (dt := _date(s.get('executed_at'))) and 0 <= (now - dt).total_seconds() <= 30*86400
                 and s.get('outcome') in {'found', 'no_results', 'blocked', 'error'}
                 and (s.get('outcome') not in {'blocked', 'error'} or s.get('diagnostic'))]
        result['markets'][platform] = dict(status=valid[-1]['outcome'] if valid else 'not_searched',
                                          searches=searches, accepted_count=0)
        if not valid:
            result['reasons'].append(platform + '_history_search_not_executed')
    if result['reasons']:
        return result
    result['status'] = 'insufficient_evidence'
    copy_records = []
    for index, sale in enumerate(data.get('sales') or []):
        if not isinstance(sale, dict):
            result['rejected'].append(dict(index=index, reasons=['invalid_sale']))
            continue
        reasons = []
        platform, url = sale.get('platform'), sale.get('url')
        checked, sold = _date(sale.get('checked_at')), _date(sale.get('sold_at'))
        if not _market_url(platform, url) or not sale.get('capture_ref'):
            reasons.append('item_source_unverified')
        if (not checked or not sold or checked < sold or not 0 <= (now-checked).total_seconds() <= 30*86400
                or not 0 <= (now-sold).total_seconds() <= 365*86400):
            reasons.append('date_missing_or_stale')
        if (sale.get('sale_status') not in {'sold', 'ended_with_bid'}
                or sale.get('actual_price_known') is not True
                or sale.get('price_basis') not in {'auction_final_price', 'fixed_price_sale', 'confirmed_best_offer'}
                or (sale.get('sale_status') == 'ended_with_bid'
                    and not (_amount(sale.get('bid_count')) or 0) > 0)):
            reasons.append('actual_completed_amount_unverified')
        from card_platform.domain.listing_title import is_multi_card_listing
        if sale.get('lot') is not False or is_multi_card_listing(str(sale.get('title') or '')):
            reasons.append('lot_not_single_card')
        price = _amount(sale.get('price'))
        currency = str(sale.get('currency') or '')
        if price is None or price <= 0 or not re.fullmatch(r'[A-Z]{3}', currency):
            reasons.append('price_or_currency_unverified')
        source_identity = sale.get('identity') or {}
        if (sale.get('identity_confirmed') is not True
            or any(k not in source_identity for k in REQUIRED_IDENTITY_ROLES) or any(
            not _same(target.get(k), source_identity.get(k)) for k in IDENTITY_FIELDS)):
            reasons.append('identity_or_version_conflict')
        images = [im for im in sale.get('card_images') or [] if isinstance(im, dict)
                  and im.get('role') in {'card_front', 'card_back'} and im.get('capture_ref')
                  and re.fullmatch(r'[0-9a-f]{64}', str(im.get('sha256') or ''))]
        if sale.get('images_reviewed') is not True or not images:
            reasons.append('item_card_images_not_reviewed')
        for key in ('condition', 'finish'):
            review = sale.get(key) or {}
            if (review.get('comparable') is not True
                    or not all(review.get(k) for k in ('target', 'candidate'))):
                reasons.append(key + '_comparison_unverified')
        shipping = _amount(sale.get('shipping'))
        if not ((sale.get('shipping_status') == 'known' and shipping is not None)
                or (sale.get('shipping_status') == 'unknown' and sale.get('shipping') is None)):
            reasons.append('shipping_not_separated')
        if sale.get('duplicate_check') not in {'distinct_copy', 'owned_copy_history'}:
            reasons.append('duplicate_or_owned_copy_history')
        copy_serial = _serial((sale.get('serial_number'), sale.get('serial_total')))
        declared_total = _amount(source_identity.get('serial_total'))
        if copy_serial and declared_total != copy_serial[1]:
            reasons.append('read_serial_total_conflicts_with_version')
        owned_serial = _serial((target.get('serial_number'), target.get('serial_total')))
        owned_copy = (sale.get('duplicate_check') == 'owned_copy_history'
                      or owned_serial is not None and copy_serial == owned_serial)
        keys = [('url', str(_item_key(platform, url))),
                *[('card_image', im['sha256']) for im in images if im['role'] == 'card_front']]
        if copy_serial:
            keys.append(('serial', str(copy_serial)))
        if sale.get('seller_id') and sale.get('consignment_code'):
            keys.append(('consignment', str((platform, sale['seller_id'], sale['consignment_code']))))
        base_price = price
        if currency != data.get('currency'):
            rates = [fx for fx in data.get('fx', []) if fx.get('base') == currency
                     and fx.get('quote') == data.get('currency') and _amount(fx.get('rate'))
                     and str(fx.get('url') or '').startswith('https://')
                     and (day := _date(str(fx.get('date')) + 'T00:00:00+00:00'))
                     and 0 <= (now-day).days <= 30]
            if not rates or price is None:
                reasons.append('dated_fx_source_required')
            else:
                base_price = price * _amount(rates[-1]['rate'])
        if reasons:
            result['rejected'].append(dict(index=index, platform=platform, url=url, reasons=reasons))
            continue
        copy_records.append(dict(keys=keys, owned=owned_copy))
        result['accepted'].append({**sale, 'price_base': float(base_price),
                                   'age_days': (now-sold).days})
    from card_platform.domain.sale_independence import cluster_copy_records
    kept, dropped = cluster_copy_records(copy_records)
    for i, anchor, reason in dropped:
        sale = result['accepted'][i]
        result['rejected'].append(dict(platform=sale['platform'],url=sale['url'],reasons=[reason]))
    result['accepted'] = [result['accepted'][i] for i in kept]
    for sale in result['accepted']:
        result['markets'][sale['platform']]['accepted_count'] += 1
    recent = [s for s in result['accepted'] if s['age_days'] <= 180]
    recent_sellers = {(s['platform'], s['seller_id']) for s in recent if s.get('seller_id')}
    if len(recent) < 3 or len(recent_sellers) < 3:
        result['reasons'].append('fewer_than_three_recent_independent_sellers')
        return result
    prices = [s['price_base'] for s in recent]
    supported_markets = {s['platform'] for s in recent}
    result.update(status='supported_observation', low_value=min(prices), fair_value=median(prices),
                  high_value=max(prices), confidence=.6 if len(supported_markets) == 1 else .65)
    result['reasons'].append('observed_public_sale_amounts_not_payment_confirmation')
    if len(supported_markets) == 1:
        result['reasons'].append('single_market_support_other_market_searched')
    return result
