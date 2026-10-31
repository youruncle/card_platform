"""Persistent, captured dual-market research; owner review is separate from MCP."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from uuid import UUID, uuid4
from urllib.parse import urlsplit

import httpx
from psycopg.types.json import Jsonb

from card_platform.domain.codex_sales_packet import prepare_packet, validate_packet
from card_platform.domain.codex_sales_research import CONTRACT_VERSION, IDENTITY_FIELDS, _market_url
from card_platform.ingestion.cardhobby import CardHobbyProvider, CARDHOBBY_SEARCH_URL
from card_platform.mcp.captures import digest
from card_platform.mcp.media import AttachmentDownloader
from card_platform.mcp.operations import OperationHandler
from card_platform.mcp.portfolio import card_revision, public_card
from card_platform.mcp.schemas import McpFailure, McpPrincipal, StrictModel
from card_platform.repository import PortfolioRepository

logger = logging.getLogger(__name__)


class StartCardResearchRequest(StrictModel):
    item_id: UUID
    expected_revision: str
    idempotency_key: UUID


class GetResearchResultRequest(StrictModel):
    run_id: UUID


class PrepareResearchPublicationRequest(StrictModel):
    run_id: UUID
    expected_revision: str
    idempotency_key: UUID


class EvidenceDownloader(AttachmentDownloader):
    """Reuse pinned-public-IP transport for non-image source text, with the same byte budget."""
    @staticmethod
    def _validate_image(content):
        return None


class ResearchTools:
    def __init__(self, database, settings, operations, *, transport=None, downloader=None):
        self.database, self.settings, self.operations = database, settings, operations
        self.root = settings.resolved_upload_dir / '.chatgpt-research'
        self.transport = transport
        self.downloader = downloader or AttachmentDownloader()
        operations.register('publish_research', OperationHandler(frozenset({'research:run','cards:write'}),
            self._publication_snapshot, self._publish))

    def directory(self, row):
        path = self.root / str(row['owner_id']) / str(row['id'])
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    async def _target(self, conn, owner_id, item_id):
        await card_revision(conn, owner_id, item_id, lock=True)
        detail = await PortfolioRepository().item_detail(conn, owner_id, item_id)
        item = public_card(detail.item)
        identity = {key: item.get(key) for key in (*IDENTITY_FIELDS, 'serial_number')}
        raw = await (await conn.execute('SELECT image_front_url,image_back_url FROM collection_item WHERE id=%s AND owner_id=%s',
            (item_id,owner_id))).fetchone()
        images = {}
        root = self.settings.resolved_upload_dir
        for side in ('front','back'):
            value = raw[f'image_{side}_url']
            if not value or not value.startswith('/uploads/'):
                raise McpFailure('INSUFFICIENT_EVIDENCE','成交研究需要可核对的当前正背原图','先补齐实卡照片并校正身份')
            path = root / value.removeprefix('/uploads/')
            if path.is_symlink() or path.resolve().parent != root.resolve():
                raise McpFailure('NOT_FOUND','原图不可用')
            try:
                images[side] = await asyncio.to_thread(path.read_bytes)
            except OSError:
                raise McpFailure('NOT_FOUND','原图不可用') from None
        binding = digest({'identity':identity,'images':{side:hashlib.sha256(body).hexdigest() for side,body in images.items()}})
        return item, identity, images, binding

    async def _owned(self, conn, owner_id, run_id, *, lock=False):
        row = await (await conn.execute('SELECT * FROM chatgpt_research_run WHERE id=%s AND owner_id=%s'+(' FOR UPDATE' if lock else ''),
            (run_id, owner_id))).fetchone()
        if row is None:
            raise McpFailure('NOT_FOUND','研究任务不存在')
        return row

    async def start_card_research(self, principal, request: StartCardResearchRequest):
        async with self.database.connection() as conn:
            return await self.start_with_connection(conn,principal,request)

    async def start_with_connection(self,conn,principal,request):
        await self.operations._grant(conn,principal,frozenset({'research:run'}))
        prior = await (await conn.execute('SELECT * FROM chatgpt_research_run WHERE owner_id=%s AND idempotency_key=%s',
            (principal.owner_id,request.idempotency_key))).fetchone()
        request_hash = digest(request.model_dump(mode='json'))
        if prior:
            if prior['request_hash'] != request_hash or prior['connection_id'] != principal.connection_id:
                raise McpFailure('IDEMPOTENCY_CONFLICT','此请求编号已用于其他研究')
            return self._view(prior)
        actual = await card_revision(conn,principal.owner_id,request.item_id,lock=True)
        if actual != request.expected_revision:
            raise McpFailure('REVISION_CONFLICT','卡片已变化','重新读取详情后启动研究')
        _, identity, images, binding = await self._target(conn,principal.owner_id,request.item_id)
        packet = prepare_packet(identity,images['front'],images['back'],request.item_id)
        row = await (await conn.execute('''INSERT INTO chatgpt_research_run
            (id,owner_id,connection_id,item_id,idempotency_key,request_hash,target_binding,packet)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''', (uuid4(),principal.owner_id,principal.connection_id,
            request.item_id,request.idempotency_key,request_hash,binding,Jsonb(packet)))).fetchone()
        return self._view(row)

    def _view(self, row):
        assessed = row['assessment'] or {}
        reviewed = row['reviewed_at'] is not None
        return {'run_id':str(row['id']), 'job_id':str(row['id']), 'item_id':str(row['item_id']),
            'status':row['status'], 'contract_version':CONTRACT_VERSION, 'created_at':row['created_at'].isoformat(),
            'finished_at':row['finished_at'].isoformat() if row['finished_at'] else None,
            'reviewed_at':row['reviewed_at'].isoformat() if reviewed else None,
            'fair_value':assessed.get('fair_value') if reviewed else None,
            'low_value':assessed.get('low_value') if reviewed else None,
            'high_value':assessed.get('high_value') if reviewed else None,
            'currency':row['packet'].get('currency'), 'packet':row['packet'],
            'assessment':assessed, 'evidence_digest':self._digest(row),
            'review_url':f'{self.operations.web_origin}/chatgpt/research/{row["id"]}',
            'error_code':row['error_code'],
            'warning':'仅为本次公开成交研究；审核、结构校验不能代替真实核图，公开落槌不等于付款。'}

    def _digest(self, row):
        # Re-read every captured byte so filesystem edits invalidate approval.
        actual = {}
        for name in row['capture_manifest']:
            if not re.fullmatch(r'[0-9a-f-]+\.(?:txt|json|bin|png)',name):
                raise McpFailure('INSUFFICIENT_EVIDENCE','捕获引用不合法')
            try:
                body = (self.directory(row)/name).read_bytes()
            except OSError:
                raise McpFailure('INSUFFICIENT_EVIDENCE','研究捕获缺失','重新检索并保存原始证据') from None
            sha = hashlib.sha256(body).hexdigest()
            if sha != row['capture_manifest'][name]['sha256']:
                raise McpFailure('REVISION_CONFLICT','研究捕获已变化','重新检索并审核')
            actual[name] = sha
        return digest({'packet':row['packet'],'captures':actual})

    async def get_result(self, principal, run_id):
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            return self._view(await self._owned(conn,principal.owner_id,run_id))

    async def get_research_result(self, principal, request):
        return await self.get_result(principal,request.run_id)

    def _capture(self, row, body, manifest, *, suffix='txt', metadata=None):
        name = f'{uuid4()}.{suffix}'
        path = self.directory(row)/name
        with path.open('xb') as stream:
            stream.write(body)
        path.chmod(0o600)
        sha = hashlib.sha256(body).hexdigest()
        manifest[name] = {'sha256':sha,**(metadata or {})}
        return name, sha

    async def _fetch(self, url, *, params=None, headers=None, transport=None):
        # Only fixed marketplace hosts/canonical item URLs enter this method.
        # Redirects and environment proxies are intentionally disabled.
        async with httpx.AsyncClient(transport=transport or self.transport, timeout=20, trust_env=False, follow_redirects=False) as client:
            async with client.stream('GET',url,params=params,headers=headers) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 4*1024*1024:
                        raise ValueError('SOURCE_SIZE_LIMIT')
                    chunks.append(chunk)
                return response.status_code,b''.join(chunks)

    async def queue_draft(self,owner_id,run_id,submitted):
        async with self.database.connection() as conn:
            async with conn.transaction():
                row=await self._owned(conn,owner_id,run_id,lock=True)
                fixed=('collection_item_id','contract_version','identity','front_sha256','back_sha256','searches','currency')
                if any(submitted.get(key)!=row['packet'].get(key) for key in fixed):
                    raise McpFailure('REVISION_CONFLICT','不能替换本次检索、目标身份或图证')
                if {s.get('platform') for s in row['packet'].get('searches',[])}!={'ebay','cardhobby'}:
                    raise McpFailure('INSUFFICIENT_EVIDENCE','请先完成本次双市场检索')
                sales=submitted.get('sales')
                if not isinstance(sales,list) or len(sales)>60 or any(not isinstance(sale,dict) or not _market_url(sale.get('platform'),sale.get('url')) for sale in sales):
                    raise McpFailure('INVALID_INPUT','候选最多60条且必须为eBay/卡淘单件商品')
                if row['status'] in {'queued','running'}:
                    if row.get('pending_packet')==submitted:
                        return self._view(row)
                    raise McpFailure('REVISION_CONFLICT','任务正在处理，请等待或刷新状态')
                await conn.execute("""UPDATE chatgpt_research_run SET pending_packet=%s,status='queued',attempt_count=0,
                    assessment='{}',reviewed_at=NULL,reviewed_digest=NULL,reviewed_session_id=NULL,
                    error_code=NULL,started_at=NULL,finished_at=NULL WHERE id=%s""",(Jsonb(submitted),run_id))
        return await self.view_for_owner(owner_id,run_id)

    async def _check_worker_connection(self,row):
        principal=McpPrincipal(owner_id=row['owner_id'],connection_id=row['connection_id'],scopes={'cards:read','research:run'})
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'research:run'}))

    async def save_draft(self, owner_id, run_id, submitted, *, lease_id=None):
        # Browser-only authoring. The model cannot claim that a human has reviewed.
        # Network captures are made here; client capture refs/price summaries are ignored.
        async with self.database.connection() as conn:
            row = await self._owned(conn,owner_id,run_id)
            if row['status'] in {'queued','running'} and (lease_id is None or row['lease_id']!=lease_id):
                raise McpFailure('REVISION_CONFLICT','检索尚未结束')
            if lease_id is not None:
                await self._check_worker_connection(row)
            original_digest = self._digest(row)
            packet = copy.deepcopy(submitted)
            fixed = ('collection_item_id','contract_version','identity','front_sha256','back_sha256','searches','currency')
            if any(packet.get(key) != row['packet'].get(key) for key in fixed):
                raise McpFailure('REVISION_CONFLICT','不能替换本次检索、目标身份或图证')
            sales = packet.get('sales')
            if not isinstance(sales,list) or len(sales)>60:
                raise McpFailure('INVALID_INPUT','候选最多60条')
            fx = packet.get('fx') or []
            if not isinstance(fx,list) or len(fx)>20 or any(not isinstance(rate,dict) for rate in fx):
                raise McpFailure('INVALID_INPUT','汇率依据格式不正确')
            manifest = dict(row['capture_manifest'])
            for sale in sales:
                if lease_id is not None:
                    await self._check_worker_connection(row)
                if not isinstance(sale,dict) or not _market_url(sale.get('platform'),sale.get('url')):
                    raise McpFailure('INVALID_INPUT','只接受eBay/卡淘的真实单件商品链接')
                if any(not isinstance(sale.get(key) or {},dict) for key in ('identity','condition','finish')):
                    raise McpFailure('INVALID_INPUT','身份、品相和版本核对格式不正确')
                canonical = urlsplit(sale['url'])
                url = f'https://{canonical.hostname}{canonical.path}'
                try:
                    status,body = await self._fetch(url)
                    if status != 200:
                        raise ValueError('ITEM_SOURCE_BLOCKED')
                except (httpx.HTTPError,ValueError):
                    raise McpFailure('SOURCE_BLOCKED','商品原文捕获失败','在来源恢复后重新保存候选') from None
                sale['capture_ref'],sale['sha256'] = self._capture(row,body,manifest,metadata={'url':url,'kind':'item'})
                sale['checked_at'] = datetime.now(timezone.utc).isoformat()
                images = sale.get('card_images') or []
                if not isinstance(images,list) or len(images)>4:
                    raise McpFailure('INVALID_INPUT','每条候选最多4张实卡照片')
                for image in images:
                    if not isinstance(image,dict) or image.get('role') not in {'card_front','card_back'} or not isinstance(image.get('url'),str):
                        raise McpFailure('INVALID_INPUT','实卡图必须有角色和公开图片链接')
                    body = await self.downloader.download(image['url'])
                    image['capture_ref'],image['sha256'] = self._capture(row,body,manifest,suffix='bin',metadata={'kind':'image'})
            for rate in fx:
                if lease_id is not None:
                    await self._check_worker_connection(row)
                if not isinstance(rate.get('url'),str):
                    raise McpFailure('INVALID_INPUT','汇率须附公开来源链接')
                body = await EvidenceDownloader().download(rate['url'])
                rate['capture_ref'],rate['sha256'] = self._capture(row,body,manifest,metadata={'kind':'fx','url':rate['url']})
            if lease_id is not None:
                await self._check_worker_connection(row)
            row['packet'],row['capture_manifest'] = packet,manifest
            assessment = validate_packet(packet,self.directory(row))
            async with conn.transaction():
                current = await self._owned(conn,owner_id,run_id,lock=True)
                if (lease_id is not None and current['lease_id']!=lease_id) or self._digest(current) != original_digest:
                    raise McpFailure('REVISION_CONFLICT','研究内容已变化','重新读取后保存')
                await conn.execute('''UPDATE chatgpt_research_run SET packet=%s,capture_manifest=%s,assessment=%s,
                    reviewed_at=NULL,reviewed_session_id=NULL,reviewed_digest=NULL,pending_packet=NULL,finished_at=now(),status='review_required' WHERE id=%s''',
                    (Jsonb(packet),Jsonb(manifest),Jsonb(assessment),run_id))
        return await self.view_for_owner(owner_id,run_id)

    async def view_for_owner(self, owner_id, run_id):
        async with self.database.connection() as conn:
            return self._view(await self._owned(conn,owner_id,run_id))

    async def approve_review(self, owner_id, run_id, evidence_digest, session_id):
        async with self.database.connection() as conn:
            async with conn.transaction():
                row = await self._owned(conn,owner_id,run_id,lock=True)
                if row['status'] in {'queued','running'}:
                    raise McpFailure('CONFIRMATION_REQUIRED','检索尚未结束')
                current = self._digest(row)
                if current != evidence_digest:
                    raise McpFailure('REVISION_CONFLICT','待审核证据已变化','重新查看全部捕获')
                _,_,_,binding = await self._target(conn,owner_id,row['item_id'])
                if binding != row['target_binding']:
                    raise McpFailure('REVISION_CONFLICT','实卡身份或照片已变化','重新发起研究')
                assessment = validate_packet(row['packet'],self.directory(row))
                # All source searches must be actual captures from this run, never old packets.
                searches = row['packet'].get('searches') or []
                if {s.get('platform') for s in searches} != {'ebay','cardhobby'} or any(
                    datetime.fromisoformat(s['executed_at']) < row['created_at'] for s in searches):
                    raise McpFailure('INSUFFICIENT_EVIDENCE','未完成本次双市场检索')
                status = 'completed' if assessment['status']=='supported_observation' else (
                    'insufficient_evidence' if assessment['status']=='insufficient_evidence' else 'blocked')
                await conn.execute('''UPDATE chatgpt_research_run SET assessment=%s,status=%s,reviewed_at=now(),
                    reviewed_session_id=%s,reviewed_digest=%s WHERE id=%s''', (Jsonb(assessment),status,session_id,current,run_id))
        return await self.view_for_owner(owner_id,run_id)

    async def _publication_snapshot(self, conn, principal, payload):
        row = await self._owned(conn,principal.owner_id,UUID(payload['run_id']),lock=True)
        evidence_digest = self._digest(row)
        if row['status']!='completed' or not row['reviewed_at'] or row['reviewed_digest']!=evidence_digest:
            raise McpFailure('INSUFFICIENT_EVIDENCE','研究未审核或证据不足','先查看本次捕获并完成审核')
        _,_,_,binding = await self._target(conn,principal.owner_id,row['item_id'])
        if binding != row['target_binding']:
            raise McpFailure('REVISION_CONFLICT','目标卡面或身份变化','重新研究')
        assessment = validate_packet(row['packet'],self.directory(row))
        if assessment['status']!='supported_observation':
            raise McpFailure('INSUFFICIENT_EVIDENCE','当前证据不再满足成交合同')
        revision = await card_revision(conn,principal.owner_id,row['item_id'],lock=True)
        return digest({'revision':revision,'evidence':evidence_digest})

    async def prepare_research_publication(self, principal, request):
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'research:run','cards:write'}))
            prior = await (await conn.execute("SELECT * FROM chatgpt_operation WHERE owner_id=%s AND kind='publish_research' AND idempotency_key=%s",
                (principal.owner_id,request.idempotency_key))).fetchone()
            if prior:
                if prior['payload'].get('request_hash')!=digest(request.model_dump(mode='json')):
                    raise McpFailure('IDEMPOTENCY_CONFLICT','此请求编号已用于其他发布')
                payload,expected = prior['payload'],prior['expected_revision']
            else:
                row = await self._owned(conn,principal.owner_id,request.run_id)
                if await card_revision(conn,principal.owner_id,row['item_id']) != request.expected_revision:
                    raise McpFailure('REVISION_CONFLICT','卡片已变化')
                payload = {'run_id':str(request.run_id),'item_id':str(row['item_id']),
                    'request_hash':digest(request.model_dump(mode='json')),'research':row['assessment']}
                expected = await self._publication_snapshot(conn,principal,payload)
        return await self.operations.prepare(principal,'publish_research',request.idempotency_key,payload,expected)

    async def _publish(self, conn, principal, payload):
        row = await self._owned(conn,principal.owner_id,UUID(payload['run_id']),lock=True)
        assessment = validate_packet(row['packet'],self.directory(row))
        result = await PortfolioRepository().add_valuation(conn,row['item_id'],principal.owner_id,
            self._valuation(row,assessment))
        await conn.execute("UPDATE valuation_snapshot SET model_version='chatgpt-captured-sales-v1' WHERE id=%s", (result,))
        return {'valuation_id':str(result),'run_id':str(row['id']),'item_id':str(row['item_id']),
            'fair_value':assessment['fair_value'],'currency':assessment['currency']}

    @staticmethod
    def _valuation(row, assessment):
        from card_platform.schemas import ValuationCreate
        return ValuationCreate(method='ai_estimate',currency=assessment['currency'],low_value=assessment['low_value'],
            fair_value=assessment['fair_value'],high_value=assessment['high_value'],confidence=min(.35,assessment['confidence']),
            evidence={'source':'chatgpt_captured_sales_research','run_id':str(row['id']),
                'contract_version':CONTRACT_VERSION,'market_research':row['packet'],
                'reviewed_at':row['reviewed_at'].isoformat(),'capture_digest':row['reviewed_digest'],
                'notes':['公开成交观察区间，不代表付款凭证；审核和原始捕获保留。']})

    def register(self, registry):
        from card_platform.mcp.server import ToolAdapter
        for name,model,handler,scopes in (
            ('start_card_research',StartCardResearchRequest,self.start_card_research,{'research:run'}),
            ('get_research_result',GetResearchResultRequest,self.get_research_result,{'cards:read'}),
            ('prepare_research_publication',PrepareResearchPublicationRequest,self.prepare_research_publication,{'research:run','cards:write'})):
            registry.register(ToolAdapter(name,{'start_card_research':'启动本次双市场实际检索并保存捕获，返回后台任务。旧估值不算新研究。',
                'get_research_result':'读取本次双市场尝试、候选、审核、缺口及价格。未审核或证据不足时价格为null。',
                'prepare_research_publication':'仅为已经由用户核图审核且满足真实成交合同的研究准备发布；不接受自填价格。'}[name],
                model,frozenset(scopes),handler,read_only=name=='get_research_result',open_world=name=='start_card_research'))


class ResearchWorker:
    def __init__(self,research,*,transport=None):
        self.research,self.transport = research,transport

    async def run(self):
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error('research worker failure type=%s',type(error).__name__)
                await asyncio.sleep(3)

    async def _heartbeat(self,run_id,lease):
        while True:
            await asyncio.sleep(20)
            async with self.research.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_research_run SET started_at=now() WHERE id=%s AND lease_id=%s AND status='running'",(run_id,lease))

    async def process_one(self,run_id=None):
        lease = uuid4()
        async with self.research.database.connection() as conn:
            await conn.execute("""UPDATE chatgpt_research_run SET status='failed',error_code='RETRY_EXHAUSTED'
                WHERE status='running' AND started_at<now()-interval '5 minutes' AND attempt_count>=3""")
            row = await (await conn.execute("""SELECT * FROM chatgpt_research_run WHERE
                (status='queued' OR (status='running' AND started_at<now()-interval '5 minutes')) AND attempt_count<3
                AND (%s::uuid IS NULL OR id=%s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",(run_id,run_id))).fetchone()
            if not row:
                return False
            await conn.execute("UPDATE chatgpt_research_run SET status='running',lease_id=%s,started_at=now(),attempt_count=attempt_count+1 WHERE id=%s",(lease,row['id']))
        principal = McpPrincipal(owner_id=row['owner_id'],connection_id=row['connection_id'],scopes={'cards:read','research:run'})
        heartbeat=asyncio.create_task(self._heartbeat(row['id'],lease))
        try:
            if row.get('pending_packet') is not None:
                await self.research.save_draft(row['owner_id'],row['id'],row['pending_packet'],lease_id=lease)
                return True
            packet = copy.deepcopy(row['packet'])
            manifest = dict(row['capture_manifest'])
            identity = packet['identity']
            query = ' '.join(str(identity.get(k) or '') for k in ('player_name','season_start','brand_name','product_name','card_number','parallel_name','grade_company','grade_value')).strip()
            # Partial completed attempts survive worker restarts; successful source captures are not repeated.
            searches = list(packet.get('searches') or [])
            for platform in ('ebay','cardhobby'):
                if any(s['platform']==platform for s in searches):
                    continue
                async with self.research.database.connection() as conn:
                    await self.research.operations._grant(conn,principal,frozenset({'research:run'}))
                    _,_,_,binding = await self.research._target(conn,row['owner_id'],row['item_id'])
                    if binding!=row['target_binding']:
                        raise McpFailure('REVISION_CONFLICT','研究目标已变化')
                if platform=='ebay':
                    url,params = 'https://www.ebay.com/sch/i.html',{'_nkw':query,'LH_Sold':1,'LH_Complete':1}
                else:
                    url,params = CARDHOBBY_SEARCH_URL,CardHobbyProvider.search_params(query,1,identity=identity)
                headers = {'Accept':'application/json, text/plain, */*','X-Requested-With':'XMLHttpRequest',
                    'Referer':'https://www.cardhobby.com.cn/market/search?ckey=0&keyword=','User-Agent':'Mozilla/5.0'} if platform=='cardhobby' else {'User-Agent':'Mozilla/5.0'}
                executed = datetime.now(timezone.utc).isoformat()
                try:
                    status,body = await self.research._fetch(url,params=params,headers=headers,transport=self.transport)
                    outcome,diagnostic = self._classify(platform,status,body)
                except (httpx.HTTPError,ValueError) as error:
                    status,body,outcome,diagnostic = None,json.dumps({'exception_type':type(error).__name__}).encode(),'error',type(error).__name__
                name,sha = self.research._capture(row,body,manifest,metadata={'url':url,'params':params,'headers':headers,'http_status':status,'kind':'search'})
                searches.append({'platform':platform,'query':query,'executed_at':executed,'outcome':outcome,
                    'diagnostic':diagnostic,'capture_ref':name,'sha256':sha,'recovery':'来源访问失败时稍后重试，候选须逐项核图审核。'})
                packet['searches'] = searches
                async with self.research.database.connection() as conn:
                    await conn.execute('''UPDATE chatgpt_research_run SET packet=%s,capture_manifest=%s
                        WHERE id=%s AND lease_id=%s''',(Jsonb(packet),Jsonb(manifest),row['id'],lease))
            assessment = validate_packet(packet,self.research.directory(row))
            async with self.research.database.connection() as conn:
                await conn.execute("""UPDATE chatgpt_research_run SET assessment=%s,status='review_required',finished_at=now(),error_code=NULL
                    WHERE id=%s AND lease_id=%s""",(Jsonb(assessment),row['id'],lease))
        except asyncio.CancelledError:
            async with self.research.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_research_run SET status='queued' WHERE id=%s AND lease_id=%s",(row['id'],lease))
            raise
        except McpFailure as error:
            async with self.research.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_research_run SET status='failed',error_code=%s,finished_at=now() WHERE id=%s AND lease_id=%s",(error.code,row['id'],lease))
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
        return True

    @staticmethod
    def _classify(platform,status,body):
        if status!=200:
            return ('blocked' if status in {401,403,429} else 'error'),f'HTTP_{status}'
        if platform=='cardhobby':
            try:
                payload = json.loads(body)
                data = payload['data']
                rows = data['PagedMarketItemList']
                if payload.get('result') not in (1,'1',True) or not isinstance(rows,list):
                    raise ValueError
                if not rows and all(data.get(k) is None for k in ('Total','TotalCount','PageIndex')):
                    return 'error','UNBOUND_SEARCH_RESPONSE'
                return ('found' if rows else 'no_results'),'待逐条打开商品、核对实卡及成交事实'
            except (ValueError,KeyError,TypeError):
                return 'error','INVALID_MARKET_RESPONSE'
        text = body.decode('utf-8','replace')
        if re.search(r'captcha|verify you|pardon our interruption|access denied',text,re.I):
            return 'blocked','CHALLENGE_OR_ACCESS_DENIED'
        if re.search(r'/itm/(?:[^/]+/)?\d+',text):
            return 'found','检索页包含候选链接，未证明成交金额或版本'
        if re.search(r'no exact matches|0 results for',text,re.I):
            return 'no_results','本次检索未返回候选，不能推断全网无成交'
        return 'error','SEARCH_CONTENT_UNVERIFIED'
