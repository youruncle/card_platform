"""Owner-scoped durable batches over existing capture and research queues."""
import base64
from datetime import datetime
import json
import re
from typing import Literal
from uuid import UUID,uuid4,uuid5

from psycopg.types.json import Jsonb
from pydantic import Field,model_validator

from card_platform.mcp.captures import GetJobStatusRequest,digest
from card_platform.mcp.portfolio import card_revision
from card_platform.mcp.research import StartCardResearchRequest
from card_platform.mcp.schemas import McpFailure,StrictModel


class BatchItemRef(StrictModel):
    job_id:UUID|None=None
    item_id:UUID|None=None
    expected_revision:str|None=None


class CreateBatchRequest(StrictModel):
    kind:Literal['capture','research']
    item_refs:list[BatchItemRef]=Field(min_length=1,max_length=20)
    idempotency_key:UUID

    @model_validator(mode='after')
    def references(self):
        for item in self.item_refs:
            if self.kind=='capture':
                if item.job_id is None or item.item_id is not None or item.expected_revision is not None:
                    raise ValueError('照片批次只接受已有识别任务编号')
            elif item.job_id is not None or item.item_id is None or not item.expected_revision:
                raise ValueError('研究批次必须提供卡片编号和当前revision')
        return self


class RetryJobRequest(StrictModel):
    job_id:UUID
    idempotency_key:UUID


class ListJobsRequest(StrictModel):
    kind:Literal['capture','research']|None=None
    cursor:str|None=Field(default=None,max_length=512)
    limit:int=Field(default=20,ge=1,le=100)


def aggregate(states):
    states=set(states)
    if 'failed' in states or 'blocked' in states:
        return 'partial_failure' if len(states)>1 else next(iter(states))
    if states&{'queued','running'}:
        return 'running' if 'running' in states or len(states)>1 else 'queued'
    if 'review_required' in states:
        return 'review_required'
    if states=={'cancelled'}:
        return 'cancelled'
    return 'succeeded'


class JobTools:
    def __init__(self,database,captures,research):
        self.database,self.captures,self.research=database,captures,research
        self.operations=(captures or research).operations

    async def _job(self,conn,principal,job_id,*,lock=False):
        suffix=' FOR UPDATE OF j' if lock else ''
        row=await (await conn.execute('''SELECT j.* FROM scanner_recognition_job j
          JOIN chatgpt_capture_job link ON link.job_id=j.id
          WHERE j.id=%s AND j.owner_id=%s AND link.owner_id=%s'''+suffix,
          (job_id,principal.owner_id,principal.owner_id))).fetchone()
        if row:
            return 'capture_job',row
        row=await (await conn.execute('SELECT j.* FROM chatgpt_capture_intake j WHERE j.id=%s AND j.owner_id=%s'+suffix,
          (job_id,principal.owner_id))).fetchone()
        if row:
            return 'intake',row
        row=await (await conn.execute('SELECT j.* FROM chatgpt_research_run j WHERE j.id=%s AND j.owner_id=%s'+suffix,
          (job_id,principal.owner_id))).fetchone()
        if row:
            return 'research',row
        raise McpFailure('NOT_FOUND','任务不存在')

    @staticmethod
    def _state(kind,row):
        if kind=='capture_job':
            return 'succeeded' if row['collection_item_id'] else ('review_required' if row['status']=='succeeded' else row['status'])
        if kind=='research':
            return {'completed':'succeeded','insufficient_evidence':'review_required'}.get(row['status'],row['status'])
        return row['status']

    @staticmethod
    def _retryable(kind,row):
        if row['status']!='failed':
            return False
        if kind=='capture_job':
            return bool(re.search(r'timeout|temporar|connection|429|rate.limit|unavailable|5\d\d|network|transport',row.get('error_message') or '',re.I))
        return row.get('error_code')=='SOURCE_BLOCKED'

    async def create_batch(self,principal,request):
        scope='cards:write' if request.kind=='capture' else 'research:run'
        payload_hash=digest(request.model_dump(mode='json'))
        async with self.database.connection() as conn:
            async with conn.transaction():
                await self.operations._grant(conn,principal,frozenset({scope}))
                prior=await (await conn.execute('SELECT * FROM chatgpt_batch WHERE owner_id=%s AND idempotency_key=%s',
                    (principal.owner_id,request.idempotency_key))).fetchone()
                if prior:
                    if prior['payload_hash']!=payload_hash or prior['connection_id']!=principal.connection_id:
                        raise McpFailure('IDEMPOTENCY_CONFLICT','此请求编号已用于其他批次')
                    batch_id=prior['id']
                else:
                    # Validate the entire scope before enqueuing any side effect.
                    for item in request.item_refs:
                        if request.kind=='capture':
                            kind,_=await self._job(conn,principal,item.job_id)
                            if kind=='research':
                                raise McpFailure('INVALID_INPUT','照片批次不能引用研究任务')
                        else:
                            if not self.research:
                                raise McpFailure('INVALID_INPUT','研究能力未启用')
                            current=await card_revision(conn,principal.owner_id,item.item_id,lock=True)
                            if current!=item.expected_revision:
                                raise McpFailure('REVISION_CONFLICT','批次中存在已变化的卡片')
                    batch_id=uuid4()
                    await conn.execute('''INSERT INTO chatgpt_batch(id,owner_id,connection_id,kind,idempotency_key,payload_hash)
                        VALUES (%s,%s,%s,%s,%s,%s)''',(batch_id,principal.owner_id,principal.connection_id,request.kind,request.idempotency_key,payload_hash))
                    for index,item in enumerate(request.item_refs):
                        job_id=item.job_id
                        if request.kind=='research':
                            started=await self.research.start_with_connection(conn,principal,StartCardResearchRequest(
                                item_id=item.item_id,expected_revision=item.expected_revision,idempotency_key=uuid5(batch_id,str(index))))
                            job_id=UUID(started['job_id'])
                        await conn.execute('''INSERT INTO chatgpt_batch_item(batch_id,owner_id,item_ref,job_ref,ordinal)
                            VALUES (%s,%s,%s,%s,%s)''',(batch_id,principal.owner_id,Jsonb(item.model_dump(mode='json',exclude_none=True)),str(job_id),index))
        return await self.get_job_status(principal,GetJobStatusRequest(batch_id=batch_id))

    async def get_job_status(self,principal,request):
        identifier=request.job_id or request.batch_id
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            batch=await (await conn.execute('SELECT * FROM chatgpt_batch WHERE id=%s AND owner_id=%s',
                (identifier,principal.owner_id))).fetchone()
            if batch:
                children=await (await conn.execute('SELECT * FROM chatgpt_batch_item WHERE batch_id=%s AND owner_id=%s ORDER BY ordinal,id',
                    (identifier,principal.owner_id))).fetchall()
            else:
                kind,row=await self._job(conn,principal,identifier)
                count=await (await conn.execute('SELECT retry_count FROM chatgpt_job_control WHERE owner_id=%s AND job_id=%s',
                    (principal.owner_id,identifier))).fetchone()
                retries=count['retry_count'] if count else 0
        if batch:
            children_views=[]
            for child in children:
                try:
                    result=await self.get_job_status(principal,GetJobStatusRequest(job_id=UUID(child['job_ref'])))
                except McpFailure as error:
                    if error.code in {'AUTH_REQUIRED','FORBIDDEN'}:
                        raise
                    result={'job_id':child['job_ref'],'status':'failed','error_code':error.code,
                            'retryable':False,'retry_count':child['retry_count']}
                children_views.append(result)
                async with self.database.connection() as conn:
                    await conn.execute('''UPDATE chatgpt_batch_item SET status=%s,retry_count=%s,retryable=%s,
                        result_ref=%s,error_code=%s,updated_at=now() WHERE id=%s AND owner_id=%s''',
                        (result['status'] if result['status']!='partial_failure' else 'failed',result.get('retry_count',0),result.get('retryable',False),
                        Jsonb({'job_id':result['job_id']}),result.get('error_code'),child['id'],principal.owner_id))
            return {'batch_id':str(batch['id']),'kind':batch['kind'],'status':aggregate([r['status'] for r in children_views]),'items':children_views}
        if kind=='research':
            result=await self.research.get_result(principal,identifier)
            result['research_status']=result['status']
            result['status']=self._state(kind,row)
        else:
            result=await self.captures.get_job_status(principal,GetJobStatusRequest(job_id=identifier))
        return {**result,'job_id':str(identifier),'kind':'research' if kind=='research' else 'capture',
                'retry_count':retries,'retryable':retries<3 and self._retryable(kind,row)}

    async def retry_job(self,principal,request):
        async with self.database.connection() as conn:
            async with conn.transaction():
                # Connection lock serializes retries, including different keys on the same job.
                await self.operations._grant(conn,principal,frozenset({'cards:read'}))
                prior=await (await conn.execute('SELECT * FROM chatgpt_job_retry WHERE owner_id=%s AND idempotency_key=%s',
                    (principal.owner_id,request.idempotency_key))).fetchone()
                if prior:
                    if prior['job_id']!=request.job_id:
                        raise McpFailure('IDEMPOTENCY_CONFLICT','此重试编号已用于其他任务')
                    kind,_=await self._job(conn,principal,request.job_id)
                    await self.operations._grant(conn,principal,frozenset({'research:run' if kind=='research' else 'cards:write'}))
                    return prior['result']
                kind,row=await self._job(conn,principal,request.job_id,lock=True)
                await self.operations._grant(conn,principal,frozenset({'research:run' if kind=='research' else 'cards:write'}))
                await conn.execute('INSERT INTO chatgpt_job_control(owner_id,job_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                    (principal.owner_id,request.job_id))
                control=await (await conn.execute('SELECT retry_count FROM chatgpt_job_control WHERE owner_id=%s AND job_id=%s FOR UPDATE',
                    (principal.owner_id,request.job_id))).fetchone()
                if control['retry_count']>=3:
                    raise McpFailure('RETRY_EXHAUSTED','此任务已重试三次','检查来源或照片后建立新任务')
                if not self._retryable(kind,row):
                    raise McpFailure('INVALID_INPUT','仅失败且可重试的任务允许重试','成功项保留；缺照片、身份冲突或审核项请按提示处理')
                table={'capture_job':'scanner_recognition_job','intake':'chatgpt_capture_intake','research':'chatgpt_research_run'}[kind]
                reset="error_message=NULL,updated_at=now(),revision=revision+1" if kind=='capture_job' else 'error_code=NULL'
                # Table and assignments come exclusively from the fixed map above.
                await conn.execute(f"UPDATE {table} SET status='queued',attempt_count=0,started_at=NULL,finished_at=NULL,{reset} WHERE id=%s AND owner_id=%s",(request.job_id,principal.owner_id))
                count=control['retry_count']+1
                await conn.execute('UPDATE chatgpt_job_control SET retry_count=%s WHERE owner_id=%s AND job_id=%s',(count,principal.owner_id,request.job_id))
                result={'job_id':str(request.job_id),'status':'queued','retry_count':count,'retryable':False}
                await conn.execute('INSERT INTO chatgpt_job_retry(owner_id,idempotency_key,job_id,result) VALUES (%s,%s,%s,%s)',
                    (principal.owner_id,request.idempotency_key,request.job_id,Jsonb(result)))
        return result

    async def list_jobs(self,principal,request):
        cursor=None
        if request.cursor:
            try:
                value=json.loads(base64.urlsafe_b64decode(request.cursor+'='*(-len(request.cursor)%4)))
                cursor=(datetime.fromisoformat(value[0]),str(UUID(value[1])),value[2])
                if cursor[0].tzinfo is None or cursor[2] not in {'batch','intake','capture_job','research'}:
                    raise ValueError
            except (ValueError,TypeError,IndexError,KeyError,UnicodeError):
                raise McpFailure('INVALID_INPUT','任务游标无效','从第一页重新查询') from None
        sql='''SELECT * FROM (
            SELECT id,created_at,'batch'::text AS resource_type,kind FROM chatgpt_batch WHERE owner_id=%s
            UNION ALL SELECT id,created_at,'intake','capture' FROM chatgpt_capture_intake WHERE owner_id=%s
            UNION ALL SELECT j.id,j.created_at,'capture_job','capture' FROM scanner_recognition_job j
              JOIN chatgpt_capture_job link ON link.job_id=j.id WHERE j.owner_id=%s AND link.owner_id=%s
            UNION ALL SELECT id,created_at,'research','research' FROM chatgpt_research_run WHERE owner_id=%s
            ) jobs WHERE (%s::text IS NULL OR kind=%s)'''
        args=[principal.owner_id]*5+[request.kind,request.kind]
        if cursor:
            sql+=' AND (created_at,id::text,resource_type)<(%s,%s,%s)'
            args+=list(cursor)
        sql+=' ORDER BY created_at DESC,id::text DESC,resource_type DESC LIMIT %s'
        args.append(request.limit+1)
        async with self.database.connection() as conn:
            await self.operations._grant(conn,principal,frozenset({'cards:read'}))
            rows=await (await conn.execute(sql,tuple(args))).fetchall()
        more=len(rows)>request.limit
        rows=rows[:request.limit]
        next_cursor=None
        if more:
            last=rows[-1]
            next_cursor=base64.urlsafe_b64encode(json.dumps([last['created_at'].isoformat(),str(last['id']),last['resource_type']]).encode()).decode().rstrip('=')
        results=[]
        for row in rows:
            identifier={'batch_id':row['id']} if row['resource_type']=='batch' else {'job_id':row['id']}
            try:
                status=await self.get_job_status(principal,GetJobStatusRequest(**identifier))
                summary={key:status[key] for key in ('status','retry_count','retryable','error_code') if key in status}
            except McpFailure as error:
                if error.code in {'AUTH_REQUIRED','FORBIDDEN'}:
                    raise
                summary={'status':'failed','error_code':error.code,'retryable':False}
            results.append({'resource_id':str(row['id']),'kind':row['kind'],'resource_type':row['resource_type'],
                'created_at':row['created_at'].isoformat(),**summary})
        return {'jobs':results,'next_cursor':next_cursor}


    def register(self,registry):
        from card_platform.mcp.server import ToolAdapter
        for name,model,handler in (
            ('create_batch',CreateBatchRequest,self.create_batch),('list_jobs',ListJobsRequest,self.list_jobs),
            ('get_job_status',GetJobStatusRequest,self.get_job_status),('retry_job',RetryJobRequest,self.retry_job)):
            registry.register(ToolAdapter(name,{'create_batch':'将1–20项照片任务归组，或启动明确卡片的研究批次。逐项保留结果，聊天结束后仍可查询。',
                'list_jobs':'分页查找本人近期批次、照片和成交研究任务，返回恢复进度所需编号。',
                'get_job_status':'按任务或批次编号恢复逐项状态、结果和审核要求；部分失败单列。',
                'retry_job':'仅重试失败且可重试的指定任务，最多3次；不重跑成功或待审核项。'}[name],
                model,frozenset({'cards:read'}),handler,read_only=name in {'list_jobs','get_job_status'}))
