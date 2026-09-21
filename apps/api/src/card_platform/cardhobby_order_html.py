"""Parse only order evidence from HTML. No scripts, contact data or raw HTML persisted."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from card_platform.purchase_cost import OrderCapture

ORIGIN = 'https://cardhobby.com.cn'


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: 'Node | None' = None

    def all(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.all(tag)

    def text(self):
        return re.sub(r'\s+', ' ', ''.join(
            child.text() if isinstance(child, Node) else child for child in self.children
        )).strip()

    def closest(self, tag):
        node = self.parent
        while node and node.tag != tag:
            node = node.parent
        return node


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('document')
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), parent=self.current)
        self.current.children.append(node)
        if tag not in {'input', 'img', 'br', 'meta', 'link', 'hr', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'col'}:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, text):
        self.current.children.append(text)

    def all(self, tag=None):
        return self.root.all(tag)

    def by_id(self, value):
        return next((n for n in self.all() if n.attrs.get('id') == value), None)


def safe_url(value, kind=None):
    parsed = urlsplit(urljoin(ORIGIN, value))
    if (parsed.scheme != 'https' or parsed.netloc != 'cardhobby.com.cn'
            or parsed.username or parsed.password):
        raise ValueError('卡淘返回异常链接，已停止读取')
    patterns = {'list': r'/order/myorders/?', 'detail': r'/order/detail/\d+/?',
                'login': r'/verify(?:/index)?/?'}
    if kind and not re.fullmatch(patterns[kind], parsed.path, re.I):
        raise ValueError('卡淘返回异常页面，已停止读取')
    return urlunsplit(parsed._replace(fragment=''))


def canonical(value):
    parsed = urlsplit(safe_url(value))
    return urlunsplit(parsed._replace(query='', fragment=''))


def recognized_list(doc):
    return any(n.attrs.get('placeholder') == '商品编号/商品名称' for n in doc.all('input'))


def amount(value, label):
    found = re.search(label + r'\s*[:：]?\s*[￥¥]\s*(\d+(?:\.\d{1,2})?)(?![\d.])', value)
    return found[1] if found else None


def detail_amounts(doc):
    values = [n.text() for n in doc.all() if n.tag in {'li', 'p', 'td', 'div'}]
    return {name: next((v for t in values if len(t) < 100 and (v := amount(t, label))), None)
            for name, label in [('paid_total', '实付总额'), ('discount', '优惠(?:金额)?'),
                                ('refund', '退款(?:金额)?'), ('extra_fee', '额外费用')]}


def parse_orders(doc):
    orders = []
    for table in doc.all('table'):
        rows = [n for n in table.all('tr') if n.closest('table') is table]
        header = next((n for n in rows if re.search(r'订单号\s*[:：]\s*\d+', n.text())), None)
        if header is None:
            continue
        date = re.search(r'\b20\d{2}-\d{2}-\d{2}\b', header.text())
        seller = next((n for n in header.all('a') if '/market/' in n.attrs.get('href', '').lower() and n.text()), None)
        data = dict(external_order_id=re.search(r'订单号\s*[:：]\s*(\d+)', header.text())[1],
                    ordered_at=date[0] if date else None, seller=seller.text() if seller else '未知',
                    fetched_at=datetime.now(timezone.utc), status='未知', shipping=None,
                    displayed_total=None, source_url=ORIGIN+'/order/myorders',
                    lines=[], parser_errors=[])
        for row in rows:
            item = re.search(r'商品编号\s*[:：]\s*(\d+)', row.text())
            if not item:
                continue
            anchors = [a for a in row.all('a') if re.fullmatch(r'/market/item/\d+/?', urlsplit(a.attrs.get('href', '')).path, re.I)]
            anchor = next((a for a in anchors if a.text()), anchors[0] if anchors else None)
            cells = [n for n in row.children if isinstance(n, Node) and n.tag == 'td']
            unit = next((m for n in cells if (m := re.search(r'[￥¥]\s*(\d+(?:\.\d{1,2})?)\s*[xX×]\s*(\d+)', n.text()))), None)
            if anchor is None or not unit or not (anchor.attrs.get('title') or anchor.text()):
                data['parser_errors'].append('商品行字段不完整')
                continue
            images = []
            for n in row.all('img'):
                p = urlsplit(urljoin(ORIGIN, n.attrs.get('src') or n.attrs.get('data-src') or ''))
                if p.scheme == 'https' and p.netloc == 'images.cardhobby.com':
                    images.append(urlunsplit(p._replace(query='', fragment='')))
            data['lines'].append(dict(item_id=item[1], title=anchor.attrs.get('title') or anchor.text(),
                                      item_url=canonical(anchor.attrs['href']), image_urls=list(dict.fromkeys(images)),
                                      unit_price=unit[1], quantity=int(unit[2])))
            for cell in cells:
                value = cell.text()
                if '含运费' in value and not any('/market/item/' in n.attrs.get('href', '').lower() for n in cell.all('a')):
                    data.update(shipping=amount(value, '含运费'), displayed_total=amount(value, '总额'))
                detail = next((a for a in cell.all('a') if a.text() == '订单详情'), None)
                if detail:
                    data['source_url'] = canonical(detail.attrs['href'])
                    status = re.search(r'退款中|已关闭|待支付|待付款|等待卖家发货|等待买家收货|待发货|待收货|待评价|已完成', value)
                    data['status'] = status[0] if status else ('待收货' if '确认收货' in value else '未知')
        if not data['lines']:
            raise ValueError('订单商品行无法读取，未填写成本')
        if not date or not seller:
            data['parser_errors'].append('订单日期或商家缺失')
        data['complete'] = not data['parser_errors']
        orders.append(OrderCapture(**data))
    headers = [n for n in doc.all('tr') if re.search(r'订单号\s*[:：]\s*\d+', n.text())]
    if len(headers) != len(orders):
        raise ValueError('部分订单未完整读取，未填写成本')
    return orders
