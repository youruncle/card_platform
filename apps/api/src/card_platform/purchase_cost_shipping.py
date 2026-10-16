"""Owner-authorized collect shipping policy, separate from marketplace payment."""
from card_platform.purchase_cost import BUNDLE, OrderCapture


async def apply_collect_shipping_policy(conn, owner, orders):
    if not any(order.shipping_method == 'sf_collect' for order in orders):
        return orders
    row = await (await conn.execute(
        'SELECT collect_shipping_default FROM cardhobby_account WHERE owner_id=%s', (owner,)
    )).fetchone()
    default = row['collect_shipping_default'] if row else None
    result = []
    for order in orders:
        if order.shipping_method != 'sf_collect':
            result.append(order)
            continue
        counts = dict(order.shipping_card_counts)
        previous = await (await conn.execute(
            'SELECT capture FROM cardhobby_purchase_order WHERE owner_id=%s AND external_order_id=%s',
            (owner, order.external_order_id),
        )).fetchone()
        # Reuse reviewed counts only for unchanged complete line evidence.
        if previous and previous['capture']['lines'] == [line.model_dump(mode='json') for line in order.lines]:
            counts = {**previous['capture'].get('shipping_card_counts', {}), **counts}
        for line in order.lines:
            if not BUNDLE.search(line.title):
                counts.setdefault(line.item_id, 1)
        counts = {line.item_id: counts[line.item_id] for line in order.lines if line.item_id in counts}
        result.append(OrderCapture.model_validate({
            **order.model_dump(), 'shipping': default,
            'shipping_basis': 'owner_default' if default is not None else 'order',
            'shipping_card_counts': counts if default is not None else {},
        }))
    return result
