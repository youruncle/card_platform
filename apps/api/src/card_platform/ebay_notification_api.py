import json

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from card_platform.config import get_settings
from card_platform.ebay_notifications import (
    EbayNotificationSignatureVerifier,
    NotificationConfigurationError,
    SignatureVerificationUnavailable,
    challenge_response,
    parse_account_deletion_notification,
    persist_and_process_notification,
    validate_notification_configuration,
)


router = APIRouter(prefix="/v1/integrations/ebay", tags=["ebay-notifications"])
signature_verifier = EbayNotificationSignatureVerifier()
MAX_NOTIFICATION_BYTES = 128 * 1024


@router.get("/marketplace-account-deletion")
async def validate_marketplace_account_deletion_endpoint(
    challenge_code: str = Query(min_length=1, max_length=512),
) -> JSONResponse:
    try:
        endpoint, token = validate_notification_configuration(get_settings())
        response = challenge_response(challenge_code, endpoint, token)
    except (NotificationConfigurationError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    return JSONResponse(content={"challengeResponse": response})


@router.post("/marketplace-account-deletion", status_code=status.HTTP_204_NO_CONTENT)
async def receive_marketplace_account_deletion(request: Request) -> Response:
    settings = get_settings()
    try:
        _, verification_token = validate_notification_configuration(settings)
    except NotificationConfigurationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    body = await request.body()
    if not body or len(body) > MAX_NOTIFICATION_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    try:
        payload = json.loads(body)
        notification = parse_account_deletion_notification(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    signature_header = request.headers.get("x-ebay-signature")
    if not signature_header and settings.ebay_notification_signature_required:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="missing signature")
    key_id = "verification-disabled"
    if settings.ebay_notification_signature_required:
        try:
            valid, key_id = await signature_verifier.verify(payload, signature_header or "", settings)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED, detail="invalid signature"
            ) from error
        except SignatureVerificationUnavailable as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="eBay signature verification is temporarily unavailable",
            ) from error
        if not valid:
            raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail="invalid signature")
    async with request.app.state.database.connection() as connection:
        await persist_and_process_notification(
            connection,
            notification,
            payload_bytes=body,
            signature_key_id=key_id,
            subject_hash_key=verification_token,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
