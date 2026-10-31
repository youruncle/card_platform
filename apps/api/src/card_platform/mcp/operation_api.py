from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.mcp.schemas import McpFailure, StrictModel

router = APIRouter(prefix="/v1/chatgpt/operations", tags=["chatgpt"])


class ApproveOperation(StrictModel):
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledgements: list[Literal["pair_images_checked"]] = Field(default_factory=list, max_length=1)


def error_response(error: McpFailure):
    code = {"AUTH_REQUIRED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404,
            "REVISION_CONFLICT": 409, "IDEMPOTENCY_CONFLICT": 409,
            "EXPIRED_OPERATION": 410}.get(error.code, 400)
    return JSONResponse(error.public(), status_code=code, headers={"Cache-Control": "private, no-store"})


@router.get("/{operation_id}")
async def preview_operation(operation_id: UUID, request: Request,
                            user: AuthenticatedUser = Depends(current_user)):
    try:
        result = await request.app.state.chatgpt_operations.preview_for_user(user.id, operation_id)
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except McpFailure as error:
        return error_response(error)


@router.post("/{operation_id}/approve")
async def approve_operation(operation_id: UUID, payload: ApproveOperation, request: Request,
                            user: AuthenticatedUser = Depends(current_user)):
    service = request.app.state.chatgpt_operations
    # Browser-controlled Origin plus JSON-only input prevents cross-site form
    # submission. Missing Origin is rejected, including for authenticated clients.
    if request.headers.get("origin") != service.web_origin:
        return error_response(McpFailure("FORBIDDEN", "请求来源未获授权", "从平台确认页操作"))
    try:
        result = await service.approve(user.id, user.session_id, operation_id, payload.payload_hash,
                                       acknowledgements=set(payload.acknowledgements))
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except McpFailure as error:
        return error_response(error)
