"""Private purchase-price queries; evidence writes never authorize ledger writes."""
import asyncio
from datetime import datetime, timezone
import logging
import re
from uuid import UUID, uuid4

import httpx
from psycopg.types.json import Jsonb
from pydantic import Field, field_validator, model_validator

from card_platform.cardhobby_account import AccountError
from card_platform.mcp.captures import digest
from card_platform.mcp.portfolio import card_revision
from card_platform.mcp.schemas import McpFailure, McpPrincipal, StrictModel
from card_platform.purchase_cost import OrderCapture, allocate_shipping, order_errors
from card_platform.purchase_cost_service import PurchaseCostService
from card_platform.repository import ConflictError, NotFoundError

logger = logging.getLogger(__name__)


class StartPurchaseLookup(StrictModel):
    item_id: UUID | None = None
    expected_revision: str | None = Field(default=None, min_length=1, max_length=128)
    keywords: list[str] = Field(default_factory=list, max_length=3)
    idempotency_key: UUID

    @field_validator('keywords')
    @classmethod
    def valid_keywords(cls, values):
        if any(not re.search(r'\w',v) or len(v.strip()) > 120 for v in values):
            raise ValueError('关键词每组1–120字符')
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode='after')
    def mode(self):
        if bool(self.item_id) == bool(self.keywords):
            raise ValueError('请选择关键词或实卡编号其中一种入口')
        if bool(self.item_id) != bool(self.expected_revision):
            raise ValueError('实卡入口需要当前revision')
        return self


class GetPurchaseLookup(StrictModel):
    lookup_id: UUID
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ListPurchaseLookups(StrictModel):
    limit: int = Field(default=20, ge=1, le=100)


CANDIDATE_FIELDS = frozenset({'unit_id','evidence_hash','order_id','ordered_at','status',
    'item_id','unit_index','title','item_url','card_price','shipping','shipping_method',
    'shipping_basis','total_cost','currency','allocation_rule','bundle_index','automatic',
    'match_method','reasons','missing','conflicts','quantity','amount_verified','fetched_at'})


class PurchaseLookupTools:
    def __init__(self, database, operations, *, service=None, account_factory=None):
        self.database, self.operations = database, operations
        self.service = service or PurchaseCostService()
        if account_factory is None:
            from card_platform.cardhobby_account_api import account_service
            account_factory = account_service
        self.account_factory = account_factory

    async def grant(self, principal):
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({'cards:read'}))

    def view(self, row, page=1, page_size=20):
        result = dict(row['result'])
        choices = result.pop('candidates', [])
        if row['status']=='completed':
            offset = (page-1)*page_size
            result.update(candidates=choices[offset:offset+page_size], total=len(choices),
                          page=page, next_page=page+1 if offset+page_size<len(choices) else None)
        return dict(lookup_id=str(row['id']), item_id=str(row['item_id']) if row['item_id'] else None,
            keywords=row['keywords'], status=row['status'], attempts=row['attempts'],
            created_at=row['created_at'].isoformat(), updated_at=row['updated_at'].isoformat(),
            error_code=row['error_code'], result=result, ledger_modified=False,
            account_url=self.operations.web_origin+'/',
            warning='本人购买记录，非市场估值；关键词仅用于召回，同款多件不代表同一实卡。')

    async def start(self, principal, request):
        request_hash = digest(request.model_dump(mode='json'))
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            prior = await (await conn.execute('SELECT * FROM chatgpt_purchase_lookup WHERE owner_id=%s AND idempotency_key=%s',
                (principal.owner_id,request.idempotency_key))).fetchone()
            if prior:
                if prior['request_hash']!=request_hash or prior['connection_id']!=principal.connection_id:
                    raise McpFailure('IDEMPOTENCY_CONFLICT','请求编号已用于其他查价参数或连接')
                if request.item_id and await card_revision(conn,principal.owner_id,request.item_id)!=request.expected_revision:
                    raise McpFailure('REVISION_CONFLICT','目标卡片已变化','重新读取详情')
                return self.view(prior)
            if request.item_id and await card_revision(conn,principal.owner_id,request.item_id,lock=True)!=request.expected_revision:
                raise McpFailure('REVISION_CONFLICT','目标卡片已变化','重新读取详情')
            count = await (await conn.execute("SELECT count(*) AS n FROM chatgpt_purchase_lookup WHERE owner_id=%s AND status IN ('queued','running')",(principal.owner_id,))).fetchone()
            if count['n']>=20:
                raise McpFailure('INVALID_INPUT','已有20个进行中的查价任务','先恢复现有任务')
            row = await (await conn.execute('''INSERT INTO chatgpt_purchase_lookup
                (owner_id,connection_id,item_id,expected_revision,keywords,idempotency_key,request_hash)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (principal.owner_id,principal.connection_id,request.item_id,request.expected_revision,
                 Jsonb(request.keywords),request.idempotency_key,request_hash))).fetchone()
        return self.view(row)

    async def get(self, principal, lookup_id, page=1, page_size=20):
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            row = await (await conn.execute('SELECT * FROM chatgpt_purchase_lookup WHERE id=%s AND owner_id=%s',
                (UUID(str(lookup_id)),principal.owner_id))).fetchone()
            if not row:
                raise McpFailure('NOT_FOUND','查价任务不存在')
            if row['item_id'] and await card_revision(conn,principal.owner_id,row['item_id'])!=row['expected_revision']:
                raise McpFailure('REVISION_CONFLICT','查价后目标卡片已变化','重新核对实卡并创建查价任务')
        return self.view(row,page,page_size)

    async def recent(self, principal, request):
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            rows = await (await conn.execute('SELECT * FROM chatgpt_purchase_lookup WHERE owner_id=%s ORDER BY created_at DESC,id DESC LIMIT %s',
                (principal.owner_id,request.limit))).fetchall()
        return {'lookups':[dict(lookup_id=str(r['id']), item_id=str(r['item_id']) if r['item_id'] else None,
            keywords=r['keywords'],status=r['status'],created_at=r['created_at'].isoformat()) for r in rows]}

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter

        async def get(principal, request):
            return await self.get(principal,request.lookup_id,request.page,request.page_size)

        for name,model,handler,description,readonly in (
            ('start_purchase_cost_lookup',StartPurchaseLookup,self.start,'按1–3组关键词直接查本人卡淘买入记录，或用item_id及revision核图查价。返回后台任务；仅写查询证据，不入账。',False),
            ('get_purchase_cost_lookup',GetPurchaseLookup,get,'分页读取本人卡淘查价结果：商品金额、运费、合计、来源和核验状态。不是市场估值，不自动写成本。',True),
            ('list_purchase_cost_lookups',ListPurchaseLookups,self.recent,'恢复本人最近卡淘买入价查询任务，可跨聊天继续。',True)):
            registry.register(ToolAdapter(name,description,model,frozenset({'cards:read'}),handler,
                read_only=readonly,open_world=name=='start_purchase_cost_lookup'))


def keyword_prices(orders, complete, keywords):
    """Prices from matching order lines, preserving payment/allocation uncertainty."""
    candidates=[]
    for order in orders:
        errors=order_errors(order)
        units=allocate_shipping(order) if not errors else []
        for line in order.lines:
            title=line.title.casefold()
            if not any(all(term in title for term in re.findall(r'[\w]+',q.casefold())) for q in keywords):
                continue
            allocated=[u for u in units if u['item_id']==line.item_id]
            # One row per bought unit; bundle prices remain explicitly whole-lot.
            for index in range(line.quantity):
                unit=next((u for u in allocated if u['unit_index']==index),None)
                candidates.append(dict(order_id=order.external_order_id,ordered_at=str(order.ordered_at) if order.ordered_at else None,
                    status=order.status,item_id=line.item_id,title=line.title,item_url=line.item_url,
                    unit_index=index,quantity=line.quantity,card_price=str(line.unit_price) if not errors else None,
                    shipping=str(unit['shipping']) if unit else None,
                    total_cost=str(unit['unit_price']+unit['shipping']) if unit else None,currency=order.currency,
                    shipping_method=order.shipping_method,shipping_basis=order.shipping_basis,
                    amount_verified=not errors,reasons=errors,fetched_at=order.fetched_at.isoformat(),
                    match_method='keyword_candidate_not_physical_identity'))
    return dict(status='candidates' if candidates else 'not_found' if complete else 'incomplete',
                candidates=candidates,search_complete=bool(complete),
                reason='关键词命中仅为本人订单候选，整包标题金额不能当成单张价；未写账本。')


class PurchaseLookupWorker:
    def __init__(self, tools):
        self.tools=tools

    async def process_one(self, lookup_id=None):
        async with self.tools.database.connection() as conn:
            row=await (await conn.execute('''SELECT * FROM chatgpt_purchase_lookup
                WHERE (status='queued' OR (status='running' AND lease_until<now()))
                AND (%s::uuid IS NULL OR id=%s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1''',
                (UUID(str(lookup_id)) if lookup_id else None,UUID(str(lookup_id)) if lookup_id else None))).fetchone()
            if not row:
                return False
            if row['attempts']>=3:
                await conn.execute("UPDATE chatgpt_purchase_lookup SET status='failed',error_code='RETRY_EXHAUSTED',updated_at=now() WHERE id=%s",(row['id'],))
                return True
            lease=uuid4()
            await conn.execute("UPDATE chatgpt_purchase_lookup SET status='running',attempts=attempts+1,lease_id=%s,lease_until=now()+interval '5 minutes',updated_at=now() WHERE id=%s",(lease,row['id']))
        principal=McpPrincipal(owner_id=row['owner_id'],connection_id=row['connection_id'],scopes={'cards:read'})
        status,error,result='completed',None,{}
        try:
            async with asyncio.timeout(240):
                await self.tools.grant(principal)
                async with self.tools.database.connection() as conn:
                    if row['item_id']:
                        if await card_revision(conn,principal.owner_id,row['item_id'])!=row['expected_revision']:
                            raise McpFailure('REVISION_CONFLICT','目标已变化')
                        plan=await self.tools.service.search_plan(conn,principal.owner_id,row['item_id'],refresh=True)
                        keywords=plan['keywords']
                    else:
                        keywords=row['keywords']
                    if not keywords:
                        raise McpFailure('INSUFFICIENT_EVIDENCE','尚无可靠查询关键词')
                    capture=await self.tools.account_factory().search(conn,principal.owner_id,keywords,prefer_live=True)
                await self.tools.grant(principal)
                orders=[OrderCapture.model_validate(o) for o in capture['orders']]
                if sum(line.quantity for order in orders for line in order.lines)>10000:
                    raise McpFailure('INVALID_INPUT','候选件数超过10000','缩小关键词范围后重新查询')
                async with self.tools.database.connection() as conn:
                    if row['item_id']:
                        resolved=await self.tools.service.resolve_search(conn,principal.owner_id,row['item_id'],orders,
                            capture['complete'],refresh=True,preview_only=True)
                        result={key:resolved[key] for key in ('status','reason','verification_issues') if key in resolved}
                        result['candidates']=[{k:v for k,v in c.items() if k in CANDIDATE_FIELDS} for c in resolved.get('candidates',[])]
                        result['matching_source']=resolved.get('trace',{}).get('recall',{}).get('source','live_search')
                    else:
                        result=keyword_prices(orders,capture['complete'],keywords)
                result.update(keywords=keywords,source='live_private_order_keyword_search',
                              searched_at=datetime.now(timezone.utc).isoformat())
                await self.tools.grant(principal)
        except asyncio.CancelledError:
            async with self.tools.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_purchase_lookup SET status='queued',updated_at=now() WHERE id=%s AND lease_id=%s",(row['id'],lease))
            raise
        except AccountError as exc:
            status='account_required' if exc.code in {'LOGIN_REQUIRED','VERIFICATION_REQUIRED','CONNECTION_REQUIRED'} else 'failed'
            error=exc.code
        except McpFailure as exc:
            status,error=('cancelled' if exc.code in {'AUTH_REQUIRED','FORBIDDEN'} else 'failed'),exc.code
        except (httpx.HTTPError,TimeoutError):
            status,error='failed','SOURCE_BLOCKED'
        except (ConflictError,NotFoundError):
            status,error='failed','TARGET_OR_EVIDENCE_CHANGED'
        except Exception as exc:
            logger.warning('purchase_lookup_failed type=%s',type(exc).__name__)
            status,error='failed','SOURCE_BLOCKED'
        async with self.tools.database.connection() as conn:
            await conn.execute('''UPDATE chatgpt_purchase_lookup SET status=%s,error_code=%s,result=%s,updated_at=now()
                WHERE id=%s AND lease_id=%s''',(status,error,Jsonb(result if status=='completed' else {}),row['id'],lease))
        return True

    async def run(self):
        while True:
            try:
                found=await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('purchase_lookup_poll_failed type=%s',type(exc).__name__)
                found=False
            if not found:
                await asyncio.sleep(2)
