"""Durable, per-new-card cost lookup shared by every ingestion client."""
import asyncio
import logging

import httpx

from psycopg.types.json import Jsonb

from card_platform.cardhobby_account import AccountError
from card_platform.purchase_cost import OrderCapture
from card_platform.repository import ConflictError, NotFoundError

logger = logging.getLogger(__name__)


async def process_automatic_cost(conn, service, account_factory=None):
    # A row lock is the lease. Process death rolls it back, leaving the job queued.
    # Owner try-lock uses the same key as existing manual cost jobs; no global lock.
    async with conn.transaction():
        result = await conn.execute(
            """SELECT * FROM automatic_purchase_cost
            WHERE status='queued' AND run_after<=now()
            ORDER BY run_after,created_at,item_id FOR UPDATE SKIP LOCKED LIMIT 1"""
        )
        job = await result.fetchone()
        if not job:
            return False
        lock = await conn.execute(
            'SELECT pg_try_advisory_xact_lock(hashtextextended(%s,68)) AS locked',
            (str(job['owner_id']),),
        )
        if not (await lock.fetchone())['locked']:
            # Let another owner's eligible task advance on the next iteration.
            await conn.execute(
                "UPDATE automatic_purchase_cost SET run_after=clock_timestamp()+interval '1 second' WHERE item_id=%s",
                (job['item_id'],),
            )
            return False
        attempts = job['attempts'] + 1
        try:
            # Savepoint keeps SQL/parser failures from aborting the task update.
            async with conn.transaction():
                async with asyncio.timeout(240):
                    outcome = await lookup(conn, service, job, account_factory)
            retry = outcome.pop('_retry', False)
            status = ('failed' if attempts >= 3 else 'queued') if retry else 'completed'
        except (ConflictError, NotFoundError):
            status = 'completed'
            outcome = {'status':'skipped', 'reason':'卡片已删除、已有交易或订单已使用，未修改成本'}
        except Exception as error:
            status = 'failed' if attempts >= 3 else 'queued'
            outcome = {'status':'error', 'reason':'自动成本获取暂时失败，未修改成本'}
            # Never log credentials, order HTML, financial data or raw exceptions.
            logger.warning('automatic_purchase_cost_failed error_type=%s attempt=%d', type(error).__name__, attempts)
        await conn.execute(
            """UPDATE automatic_purchase_cost SET status=%s, attempts=%s, result=%s,
            run_after=clock_timestamp()+interval '60 seconds', updated_at=clock_timestamp() WHERE item_id=%s""",
            (status, attempts, Jsonb(outcome), job['item_id']),
        )
        return True


async def lookup(conn, service, job, account_factory):
    owner, item = job['owner_id'], job['item_id']
    plan = await service.search_plan(conn, owner, item, refresh=False)
    if plan['status'] == 'skipped':
        return {'status':'skipped', 'reason':plan['reason']}
    if account_factory is None:
        # Share the API's configured encrypted account service without importing
        # its router at repository/module initialization time.
        from card_platform.cardhobby_account_api import account_service
        account_factory = account_service
    try:
        async with asyncio.timeout(150):
            capture = await account_factory().search(conn, owner, plan['keywords'])
    except AccountError as error:
        # Commit login/cooldown/challenge state and stop; never send an SMS here.
        if error.code in {'NETWORK_ERROR', 'RATE_LIMITED', 'UNAVAILABLE'}:
            return {'status':'error', 'code':error.code, '_retry':True,
                    'reason':'卡淘服务暂时不可用，未修改成本'}
        return {'status':'account_required', 'code':error.code,
                'reason':'卡淘账号需要连接或验证，请在网页成本入口处理后重试'}
    except (httpx.HTTPError, TimeoutError):
        # The account service may already have persisted a login cooldown. Keep
        # it on network failures exactly as its HTTP API does.
        return {'status':'error', '_retry':True, 'reason':'卡淘网络暂时不可用，未修改成本'}
    orders = [OrderCapture.model_validate(order) for order in capture['orders']]
    resolved = await service.resolve_search(conn, owner, item, orders, capture['complete'], refresh=False)
    # Detailed order evidence stays in the existing cost audit storage.
    return {key:resolved[key] for key in ('status', 'reason', 'trace') if key in resolved}
