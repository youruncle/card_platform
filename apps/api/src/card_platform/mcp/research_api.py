"""Session-only review of captured evidence. Never registered as MCP tools."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import Field

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.mcp.operation_api import error_response
from card_platform.mcp.schemas import McpFailure, StrictModel

router = APIRouter(prefix='/v1/chatgpt/research',tags=['chatgpt'])
HEADERS = {'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"}


class ResearchDraft(StrictModel):
    packet: dict


class ReviewResearch(StrictModel):
    evidence_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewed_all_candidates: Literal[True]


def service(request):
    return request.app.state.chatgpt_mcp_server.research_tools


def origin(request):
    if request.headers.get('origin') != service(request).operations.web_origin:
        raise McpFailure('FORBIDDEN','请从平台研究审核页操作')


@router.get('/{run_id}')
async def research_preview(run_id:UUID,request:Request,user:AuthenticatedUser=Depends(current_user)):
    try:
        tools=service(request)
        result=await tools.view_for_owner(user.id,run_id)
        async with tools.database.connection() as conn:
            row=await tools._owned(conn,user.id,run_id)
            result['captures']=[{'ref':name,'kind':meta['kind'],'source_url':meta.get('url'),
                'url':f'/v1/chatgpt/research/{run_id}/captures/{name}'} for name,meta in row['capture_manifest'].items()]
        return JSONResponse(result,headers=HEADERS)
    except McpFailure as error:
        return error_response(error)


@router.get('/{run_id}/captures/{capture_ref}')
async def captured_evidence(run_id:UUID,capture_ref:str,request:Request,user:AuthenticatedUser=Depends(current_user)):
    try:
        tools=service(request)
        async with tools.database.connection() as conn:
            row=await tools._owned(conn,user.id,run_id)
        tools._digest(row)
        meta=row['capture_manifest'].get(capture_ref)
        if not meta:
            raise McpFailure('NOT_FOUND','捕获不存在')
        path=tools.directory(row)/capture_ref
        if meta['kind']=='image':
            from card_platform.image_service import detect_image_content_type
            return FileResponse(path,media_type=detect_image_content_type(path.read_bytes()),headers=HEADERS)
        # Source HTML is displayed as text and never executed in our origin.
        return PlainTextResponse(path.read_text(errors='replace'),headers=HEADERS)
    except McpFailure as error:
        return error_response(error)


@router.post('/{run_id}/draft')
async def save_draft(run_id:UUID,payload:ResearchDraft,request:Request,user:AuthenticatedUser=Depends(current_user)):
    try:
        origin(request)
        return JSONResponse(await service(request).queue_draft(user.id,run_id,payload.packet),headers=HEADERS)
    except McpFailure as error:
        return error_response(error)


@router.post('/{run_id}/review')
async def review_research(run_id:UUID,payload:ReviewResearch,request:Request,user:AuthenticatedUser=Depends(current_user)):
    try:
        origin(request)
        return JSONResponse(await service(request).approve_review(user.id,run_id,payload.evidence_digest,user.session_id),headers=HEADERS)
    except McpFailure as error:
        return error_response(error)
