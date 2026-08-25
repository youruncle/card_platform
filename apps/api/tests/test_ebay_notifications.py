import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from card_platform.config import Settings
from card_platform.ebay_notifications import (
    EbayNotificationSignatureVerifier,
    NotificationConfigurationError,
    challenge_response,
    decode_signature_header,
    parse_account_deletion_notification,
    validate_notification_configuration,
)


ENDPOINT = (
    "https://example.ngrok-free.dev/v1/integrations/ebay/marketplace-account-deletion"
)
TOKEN = "a" * 32


def _payload() -> dict:
    return {
        "metadata": {
            "topic": "MARKETPLACE_ACCOUNT_DELETION",
            "schemaVersion": "1.0",
            "deprecated": False,
        },
        "notification": {
            "notificationId": "notification-1",
            "eventDate": "2026-08-24T01:00:00.000Z",
            "publishDate": "2026-08-24T01:00:01.000Z",
            "publishAttemptCount": 1,
            "data": {
                "username": "deleted-user",
                "userId": "immutable-user-id",
                "eiasToken": "legacy-token",
            },
        },
    }


def test_challenge_response_uses_ebay_required_order() -> None:
    expected = hashlib.sha256(f"challenge{TOKEN}{ENDPOINT}".encode()).hexdigest()
    assert challenge_response("challenge", ENDPOINT, TOKEN) == expected


def test_notification_configuration_requires_public_https_and_valid_token() -> None:
    settings = Settings(
        ebay_marketplace_deletion_endpoint=ENDPOINT,
        ebay_marketplace_deletion_verification_token=TOKEN,
    )
    assert validate_notification_configuration(settings) == (ENDPOINT, TOKEN)

    with pytest.raises(NotificationConfigurationError, match="HTTPS"):
        validate_notification_configuration(
            Settings(
                ebay_marketplace_deletion_endpoint="http://localhost:8000/callback",
                ebay_marketplace_deletion_verification_token=TOKEN,
            )
        )


def test_account_deletion_payload_is_strictly_parsed() -> None:
    parsed = parse_account_deletion_notification(_payload())
    assert parsed.notification_id == "notification-1"
    assert parsed.publish_attempt_count == 1
    assert parsed.subject_values == (
        "deleted-user",
        "immutable-user-id",
        "legacy-token",
    )

    payload = _payload()
    payload["metadata"]["topic"] = "OTHER_TOPIC"
    with pytest.raises(ValueError, match="unsupported"):
        parse_account_deletion_notification(payload)


@pytest.mark.asyncio
async def test_official_ecc_signature_format_is_verified() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    payload = _payload()
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = private_key.sign(message, ec.ECDSA(hashes.SHA1()))
    signature_payload = {
        "alg": "ECDSA",
        "kid": "test-key",
        "signature": base64.b64encode(signature).decode(),
        "digest": "SHA1",
    }
    header = base64.b64encode(json.dumps(signature_payload).encode()).decode()
    assert decode_signature_header(header)["kid"] == "test-key"

    verifier = EbayNotificationSignatureVerifier()
    verifier._public_keys["test-key"] = (
        datetime.now(timezone.utc) + timedelta(hours=1),
        public_pem,
    )
    valid, key_id = await verifier.verify(payload, header, Settings())
    assert valid is True
    assert key_id == "test-key"

    payload["notification"]["data"]["username"] = "tampered"
    valid, _ = await verifier.verify(payload, header, Settings())
    assert valid is False
