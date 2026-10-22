"""Bounded history preparation, reusing the normal purchase accounting service."""
import asyncio

from card_platform.purchase_cost import OrderCapture, evidence_hash
from card_platform.purchase_cost_archive import PurchaseArchive, fingerprint
from card_platform.purchase_cost_images import PurchaseImageObserver
from card_platform.purchase_cost_catalog import extract_product


class PurchaseHistoryService:
    def __init__(self, settings, account, costs):
        self.settings, self.account, self.costs = settings, account, costs

    async def sync_page(self, conn, owner, *, restart=False):
        archive = PurchaseArchive.configured(self.settings, owner)
        if restart:
            archive.reset()
        state = archive.state()
        if state['pages'] and not archive.fresh():
            # Resuming old pages must not renew the financial snapshot's age.
            archive.reset()
            state = archive.state()
        if state['complete']:
            if await self.account.archive_orders(conn, owner) is not None:
                return dict(complete=True, pages=state['pages'], orders=len(state['order_keys']),
                            captured_at=state['captured_at'])
            archive.reset()
            state = archive.state()
        cursor = state['next_cursor']
        page = await self.account.history_page(conn, owner, cursor)
        orders = [OrderCapture.model_validate(row) for row in page['orders']]
        await self.costs.sync(conn, owner, orders)
        state = archive.save_page(page, cursor)
        return dict(complete=state['complete'], pages=state['pages'], orders=len(state['order_keys']),
                    captured_at=state['captured_at'])

    async def prepare(self, conn, owner, *, limit=2, retry_failed=False):
        if not 1 <= limit <= 10:
            raise ValueError('product preparation budget')
        orders = await self.account.archive_orders(conn, owner, require_fresh=False)
        if orders is None:
            raise ValueError('完整历史资料尚未就绪或绑定账号已改变')
        archive = PurchaseArchive.configured(self.settings, owner)
        revisions = sorted((row['external_order_id'], evidence_hash(OrderCapture.model_validate(row)))
                           for row in orders)
        key = fingerprint(['product-preparation', archive.state()['account_key'], revisions])
        progress = archive.read('runs', key) or dict(done=[], failed={})
        if retry_failed:
            progress['done'] = [k for k in progress['done'] if k not in progress['failed']]
            progress['failed'] = {}
        lines = {archive.line_key(line):line for order in orders for line in order['lines']}
        # Successful preparation is valid only while its actual source images
        # remain available and fresh. Failed work stays explicit until retried.
        progress['done'] = [k for k in progress['done'] if k in lines and
                            (k in progress['failed'] or archive.product(lines[k], verify_images=False) is not None)]
        observer = PurchaseImageObserver(self.settings)
        observer.archive = archive
        selected = [(k,line) for k,line in lines.items() if k not in progress['done']][:limit]
        slots = asyncio.Semaphore(2)

        async def prepare_one(line_key, line):
            stage = 'gallery'
            try:
                async with asyncio.timeout(100):
                    gallery, images = await observer.gallery_bytes(line)
                    stage = 'transcription'
                    product = await extract_product(archive, observer, line, gallery, images)
                archive.write('runs', fingerprint(['product-index', line_key]), product)
            except Exception as error:
                known = {'product field semantics unverified','product field photo unverified',
                         'product photo scope incomplete','product photo budget',
                         'purchase_gallery_unbound_or_over_budget','purchase_item_binding_missing',
                         'unbound purchase gallery','product response budget','photo_bytes',
                         'product repair dropped identity observation',
                         'photo_dimensions_or_format','response_too_large','redirect_not_allowed',
                         'unapproved_photo_url'}
                reason = str(error) if type(error) is ValueError and str(error) in known else type(error).__name__
                progress['failed'][line_key] = stage+':'+reason
            progress['done'].append(line_key)
            # Every product is durable even if a later model call is cancelled.
            archive.write('runs', key, progress)

        async def bounded(line_key, line):
            async with slots:
                await prepare_one(line_key,line)

        async with asyncio.TaskGroup() as group:
            for line_key,line in selected:
                group.create_task(bounded(line_key,line))
        return dict(total=len(lines), attempted=len(selected), processed=len(progress['done']),
                    remaining=len(lines)-len(progress['done']), failed=len(progress['failed']),
                    ready_products=len(progress['done'])-len(progress['failed']))
