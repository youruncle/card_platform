from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import quote, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import httpx
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import Settings


EBAY_APPLICATION_SCOPE = "https://api.ebay.com/oauth/api_scope"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,80}")


class NotificationConfigurationError(ValueError):
    pass


class SignatureVerificationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountDeletionNotification:
    notification_id: str
    event_date: datetime | None
    publish_date: datetime | None
    publish_attempt_count: int
    subject_values: tuple[str, ...]


def validate_notification_configuration(settings: Settings) -> tuple[str, str]:
    endpoint = (settings.ebay_marketplace_deletion_endpoint or "").strip()
    token = (settings.ebay_marketplace_deletion_verification_token or "").strip()
    parsed = urlparse(endpoint)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise NotificationConfigurationError("eBay notification endpoint must be a public HTTPS URL")
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local"):
        raise NotificationConfigurationError("eBay notification endpoint cannot use localhost")
    if not TOKEN_PATTERN.fullmatch(token):
        raise NotificationConfigurationError(
            "eBay verification token must be 32-80 alphanumeric, underscore, or hyphen characters"
        )
    return endpoint, token


def challenge_response(challenge_code: str, endpoint: str, verification_token: str) -> str:
    if not challenge_code or len(challenge_code) > 512:
        raise ValueError("invalid challenge code")
    digest = hashlib.sha256()
    digest.update(challenge_code.encode())
    digest.update(verification_token.encode())
    digest.update(endpoint.encode())
    return digest.hexdigest()


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid notification timestamp") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_account_deletion_notification(payload: object) -> AccountDeletionNotification:
    if not isinstance(payload, dict):
        raise ValueError("notification body must be a JSON object")
    metadata = payload.get("metadata")
    notification = payload.get("notification")
    if not isinstance(metadata, dict) or not isinstance(notification, dict):
        raise ValueError("notification metadata and notification are required")
    if metadata.get("topic") != "MARKETPLACE_ACCOUNT_DELETION":
        raise ValueError("unsupported eBay notification topic")
    notification_id = str(notification.get("notificationId") or "").strip()
    if not notification_id or len(notification_id) > 200:
        raise ValueError("notificationId is required")
    try:
        attempt_count = int(notification.get("publishAttemptCount") or 1)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid publishAttemptCount") from error
    data = notification.get("data")
    if not isinstance(data, dict):
        raise ValueError("notification data is required")
    subject_values = tuple(
        dict.fromkeys(
            value
            for key in ("username", "userId", "eiasToken")
            if (value := str(data.get(key) or "").strip())
        )
    )
    if not subject_values:
        raise ValueError("notification contains no eBay subject identifier")
    return AccountDeletionNotification(
        notification_id=notification_id,
        event_date=_timestamp(notification.get("eventDate")),
        publish_date=_timestamp(notification.get("publishDate")),
        publish_attempt_count=max(1, attempt_count),
        subject_values=subject_values,
    )


def decode_signature_header(signature_header: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(signature_header) % 4)
        decoded = base64.b64decode(signature_header + padding, validate=True)
        result = json.loads(decoded.decode("ascii"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid x-ebay-signature header") from error
    if not isinstance(result, dict) or not result.get("kid") or not result.get("signature"):
        raise ValueError("x-ebay-signature is missing kid or signature")
    return result


class EbayNotificationSignatureVerifier:
    def __init__(self) -> None:
        self._public_keys: dict[str, tuple[datetime, str]] = {}

    async def _application_token(self, settings: Settings, client: httpx.AsyncClient) -> str:
        if not settings.ebay_client_id or not settings.ebay_client_secret:
            raise SignatureVerificationUnavailable("eBay client credentials are not configured")
        credentials = base64.b64encode(
            f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()
        ).decode()
        try:
            response = await client.post(
                settings.ebay_token_url,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": EBAY_APPLICATION_SCOPE},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise SignatureVerificationUnavailable("unable to obtain eBay application token") from error
        token = response.json().get("access_token")
        if not token:
            raise SignatureVerificationUnavailable("eBay token response did not contain access_token")
        return str(token)

    async def _public_key(
        self, key_id: str, settings: Settings, client: httpx.AsyncClient
    ) -> str:
        cached = self._public_keys.get(key_id)
        now = datetime.now(timezone.utc)
        if cached and cached[0] > now:
            return cached[1]
        token = await self._application_token(settings, client)
        url = f"{settings.ebay_notification_public_key_url.rstrip('/')}/{quote(key_id, safe='')}"
        try:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise SignatureVerificationUnavailable("unable to retrieve eBay notification key") from error
        key = str(response.json().get("key") or "").strip()
        if not key:
            raise SignatureVerificationUnavailable("eBay public key response was empty")
        key = key.replace("-----BEGIN PUBLIC KEY-----", "-----BEGIN PUBLIC KEY-----\n")
        key = key.replace("-----END PUBLIC KEY-----", "\n-----END PUBLIC KEY-----")
        self._public_keys[key_id] = (now + timedelta(hours=1), key)
        return key

    async def verify(
        self, payload: dict[str, Any], signature_header: str, settings: Settings
    ) -> tuple[bool, str]:
        signature = decode_signature_header(signature_header)
        key_id = str(signature["kid"])
        try:
            signature_bytes = base64.b64decode(str(signature["signature"]), validate=True)
        except ValueError:
            return False, key_id
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            key_text = await self._public_key(key_id, settings, client)
        try:
            public_key = serialization.load_pem_public_key(key_text.encode())
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                return False, key_id
            public_key.verify(signature_bytes, message, ec.ECDSA(hashes.SHA1()))
        except (ValueError, TypeError, InvalidSignature):
            return False, key_id
        return True, key_id


def _subject_hashes(values: tuple[str, ...], key: str) -> list[str]:
    return [hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest() for value in values]


async def persist_and_process_notification(
    connection: AsyncConnection,
    notification: AccountDeletionNotification,
    *,
    payload_bytes: bytes,
    signature_key_id: str,
    subject_hash_key: str,
) -> tuple[int, int]:
    predicates = " OR ".join(
        "position(%s in raw_payload::text) > 0" for _ in notification.subject_values
    )
    evidence_result = await connection.execute(
        f"""
        DELETE FROM market_evidence
        WHERE provider_code IN ('ebay', 'ebay_public')
          AND ({predicates})
        RETURNING identity_fingerprint
        """,
        notification.subject_values,
    )
    deleted_evidence_rows = await evidence_result.fetchall()
    affected_fingerprints = {row["identity_fingerprint"] for row in deleted_evidence_rows}
    comp_result = await connection.execute(
        f"""
        DELETE FROM market_comp mc
        USING data_source ds
        WHERE mc.data_source_id = ds.id
          AND ds.code IN ('ebay', 'ebay_public')
          AND ({predicates.replace('raw_payload', 'mc.raw_payload')})
        RETURNING mc.id
        """,
        notification.subject_values,
    )
    deleted_comp_count = len(await comp_result.fetchall())
    if affected_fingerprints:
        await connection.execute(
            """
            UPDATE market_identity_cache cache
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence evidence
                    WHERE evidence.identity_fingerprint = cache.identity_fingerprint
                ),
                updated_at = now()
            WHERE identity_fingerprint = ANY(%s)
            """,
            (list(affected_fingerprints),),
        )
    deleted_evidence_count = len(deleted_evidence_rows)
    await connection.execute(
        """
        INSERT INTO ebay_account_deletion_notification (
            notification_id, event_date, publish_date, publish_attempt_count,
            payload_sha256, subject_hashes, signature_key_id, signature_valid,
            processing_status, deleted_market_evidence_count,
            deleted_market_comp_count, processed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, true, 'processed', %s, %s, now())
        ON CONFLICT (notification_id) DO UPDATE SET
            publish_attempt_count = GREATEST(
                ebay_account_deletion_notification.publish_attempt_count,
                EXCLUDED.publish_attempt_count
            ),
            last_received_at = now()
        """,
        (
            notification.notification_id,
            notification.event_date,
            notification.publish_date,
            notification.publish_attempt_count,
            hashlib.sha256(payload_bytes).hexdigest(),
            Jsonb(_subject_hashes(notification.subject_values, subject_hash_key)),
            signature_key_id,
            deleted_evidence_count,
            deleted_comp_count,
        ),
    )
    return deleted_evidence_count, deleted_comp_count
