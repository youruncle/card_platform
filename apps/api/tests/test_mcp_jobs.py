import asyncio
from uuid import UUID,uuid4

from pydantic import ValidationError
import pytest

from card_platform.mcp.schemas import McpFailure
from test_mcp_captures import captures_case as shared_captures_case, upload, process, successful_job
from test_mcp_research import research_case as shared_research_case, mutation_case as shared_mutation_case

captures_case=shared_captures_case
research_case=shared_research_case
mutation_case=shared_mutation_case
pytestmark=pytest.mark.asyncio


async def test_capture_batch_is_idempotent_owner_scoped_and_preserves_success_on_retry(captures_case):
    from card_platform.mcp.jobs import JobTools,CreateBatchRequest,RetryJobRequest,ListJobsRequest
    from card_platform.mcp.captures import GetJobStatusRequest
    captures,principal,_=captures_case
    intake=await captures.prepare_card_import(principal,upload(3))
    await process(captures,intake['job_id'])
    current=await captures.get_job_status(principal,GetJobStatusRequest(job_id=intake['job_id']))
    ids=[job['job_id'] for job in current['jobs']]
    jobs=JobTools(captures.database,captures,None)
    request=CreateBatchRequest(kind='capture',item_refs=[{'job_id':job_id} for job_id in ids],idempotency_key=uuid4())
    batch=await jobs.create_batch(principal,request)
    assert (await jobs.create_batch(principal,request))['batch_id']==batch['batch_id']
    await successful_job(captures,principal,ids[0])
    async with captures.database.connection() as conn:
        await conn.execute("UPDATE scanner_recognition_job SET status='failed',error_message='TimeoutError: upstream timeout' WHERE id=%s",(UUID(ids[1]),))
    status=await jobs.get_job_status(principal,GetJobStatusRequest(batch_id=batch['batch_id']))
    assert status['status']=='partial_failure'
    assert [item['status'] for item in status['items']]==['review_required','failed','queued']
    key=uuid4()
    retry=RetryJobRequest(job_id=ids[1],idempotency_key=key)
    results=await asyncio.gather(*(jobs.retry_job(principal,retry) for _ in range(3)))
    assert {r['retry_count'] for r in results}=={1}
    status=await jobs.get_job_status(principal,GetJobStatusRequest(batch_id=batch['batch_id']))
    assert status['items'][0]['status']=='review_required'
    assert status['items'][1]['status']=='queued'
    with pytest.raises(McpFailure):
        await jobs.retry_job(principal,RetryJobRequest(job_id=ids[0],idempotency_key=uuid4()))
    other=principal.model_copy(update={'owner_id':uuid4()})
    with pytest.raises(McpFailure):
        await jobs.get_job_status(other,GetJobStatusRequest(batch_id=batch['batch_id']))
    first=await jobs.list_jobs(principal,ListJobsRequest(limit=1))
    assert first['next_cursor']
    second=await jobs.list_jobs(principal,ListJobsRequest(limit=1,cursor=first['next_cursor']))
    assert first['jobs'][0]['resource_id']!=second['jobs'][0]['resource_id']


async def test_retry_limit_non_retryable_errors_and_same_key_different_target(captures_case):
    from card_platform.mcp.jobs import JobTools,RetryJobRequest
    from card_platform.mcp.captures import GetJobStatusRequest
    captures,principal,_=captures_case
    intake=await captures.prepare_card_import(principal,upload())
    await process(captures,intake['job_id'])
    child=(await captures.get_job_status(principal,GetJobStatusRequest(job_id=intake['job_id'])))['jobs'][0]['job_id']
    jobs=JobTools(captures.database,captures,None)
    for count in range(1,4):
        async with captures.database.connection() as conn:
            await conn.execute("UPDATE scanner_recognition_job SET status='failed',error_message='TimeoutError' WHERE id=%s",(UUID(child),))
        result=await jobs.retry_job(principal,RetryJobRequest(job_id=child,idempotency_key=uuid4()))
        assert result['retry_count']==count
    async with captures.database.connection() as conn:
        await conn.execute("UPDATE scanner_recognition_job SET status='failed',error_message='TimeoutError' WHERE id=%s",(UUID(child),))
    with pytest.raises(McpFailure) as error:
        await jobs.retry_job(principal,RetryJobRequest(job_id=child,idempotency_key=uuid4()))
    assert error.value.code=='RETRY_EXHAUSTED'
    async with captures.database.connection() as conn:
        await conn.execute("UPDATE scanner_recognition_job SET error_message='INVALID_IMAGE: bad format' WHERE id=%s",(UUID(child),))
    with pytest.raises(McpFailure):
        await jobs.retry_job(principal,RetryJobRequest(job_id=child,idempotency_key=uuid4()))


async def test_research_batch_checks_all_targets_before_any_new_job(research_case):
    from card_platform.mcp.jobs import JobTools,CreateBatchRequest
    from test_mcp_mutations import revision
    research,tools,principal,item_id=research_case
    jobs=JobTools(tools.database,None,research)
    good={'item_id':str(item_id),'expected_revision':await revision(tools,principal,item_id)}
    bad={'item_id':str(uuid4()),'expected_revision':'unknown'}
    async with tools.database.connection() as conn:
        before=(await (await conn.execute('SELECT count(*) AS n FROM chatgpt_research_run WHERE owner_id=%s',(principal.owner_id,))).fetchone())['n']
    with pytest.raises(McpFailure):
        await jobs.create_batch(principal,CreateBatchRequest(kind='research',item_refs=[good,bad],idempotency_key=uuid4()))
    async with tools.database.connection() as conn:
        assert (await (await conn.execute('SELECT count(*) AS n FROM chatgpt_research_run WHERE owner_id=%s',(principal.owner_id,))).fetchone())['n']==before
    request=CreateBatchRequest(kind='research',item_refs=[good],idempotency_key=uuid4())
    batch=await jobs.create_batch(principal,request)
    assert len(batch['items'])==1
    assert batch['items'][0]['job_id']
    with pytest.raises(McpFailure) as error:
        await jobs.create_batch(principal,request.model_copy(update={'item_refs':request.item_refs*2}))
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


async def test_batch_limits_and_reference_types_are_explicit():
    from card_platform.mcp.jobs import CreateBatchRequest,ListJobsRequest
    for count in (0,21):
        with pytest.raises(ValidationError):
            CreateBatchRequest(kind='capture',item_refs=[{'job_id':str(uuid4())}]*count,idempotency_key=uuid4())
    with pytest.raises(ValidationError):
        CreateBatchRequest(kind='capture',item_refs=[{'item_id':str(uuid4()),'expected_revision':'r'}],idempotency_key=uuid4())
    with pytest.raises(ValidationError):
        ListJobsRequest(limit=101)


async def test_batch_reports_one_missing_photo_without_losing_other_item_progress(captures_case):
    from card_platform.mcp.jobs import JobTools,CreateBatchRequest
    from card_platform.mcp.captures import GetJobStatusRequest
    captures,principal,_=captures_case
    intake=await captures.prepare_card_import(principal,upload(2))
    await process(captures,intake['job_id'])
    children=(await captures.get_job_status(principal,GetJobStatusRequest(job_id=intake['job_id'])))['jobs']
    jobs=JobTools(captures.database,captures,None)
    batch=await jobs.create_batch(principal,CreateBatchRequest(kind='capture',item_refs=[{'job_id':j['job_id']} for j in children],idempotency_key=uuid4()))
    async with captures.database.connection() as conn:
        row=await captures._owned_capture(conn,principal,UUID(children[1]['job_id']))
    captures.capture.queue.image_path(row['front_image_path']).unlink()
    result=await jobs.get_job_status(principal,GetJobStatusRequest(batch_id=batch['batch_id']))
    assert result['status']=='partial_failure'
    assert result['items'][0]['status']=='queued'
    assert result['items'][1]['status']=='failed'
    assert result['items'][1]['error_code']=='NOT_FOUND'
