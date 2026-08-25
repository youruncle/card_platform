import json
import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.db import Database
from card_platform.ebay_notifications import (
    parse_account_deletion_notification,
    persist_and_process_notification,
)


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


@pytest.mark.asyncio
async def test_verified_deletion_is_idempotent_and_does_not_retain_subject_plaintext() -> None:
    database = Database(
        Settings(
            database_url=os.environ["TEST_DATABASE_URL"],
            database_pool_min_size=1,
            database_pool_max_size=2,
        )
    )
    await database.open()
    subject = f"deleted-ebay-user-{uuid4()}"
    notification_id = f"notification-{uuid4()}"
    fingerprint = f"ebay-deletion-test:{uuid4()}"
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {
            "notificationId": notification_id,
            "publishAttemptCount": 1,
            "data": {"username": subject},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    notification = parse_account_deletion_notification(payload)
    try:
        async with database.connection() as connection:
            await connection.execute("BEGIN")
            try:
                await connection.execute(
                    """
                    INSERT INTO market_identity_cache (
                        identity_fingerprint, normalized_identity
                    ) VALUES (%s, '{}'::jsonb)
                    """,
                    (fingerprint,),
                )
                await connection.execute(
                    """
                    INSERT INTO market_evidence (
                        identity_fingerprint, provider_code, external_sale_id,
                        raw_title, sale_price, currency, occurred_at, source_url,
                        evidence_level, raw_payload
                    ) VALUES (%s, 'ebay', %s, 'Deletion fixture', 1, 'USD', now(),
                              'https://www.ebay.com/itm/test', 'confirmed_transaction', %s)
                    """,
                    (fingerprint, f"sale-{uuid4()}", Jsonb({"seller": {"username": subject}})),
                )
                deleted_evidence, deleted_comps = await persist_and_process_notification(
                    connection,
                    notification,
                    payload_bytes=body,
                    signature_key_id="verified-test-key",
                    subject_hash_key="test-hmac-key-that-is-long-enough",
                )
                assert (deleted_evidence, deleted_comps) == (1, 0)
                repeated = await persist_and_process_notification(
                    connection,
                    notification,
                    payload_bytes=body,
                    signature_key_id="verified-test-key",
                    subject_hash_key="test-hmac-key-that-is-long-enough",
                )
                assert repeated == (0, 0)
                result = await connection.execute(
                    """
                    SELECT deleted_market_evidence_count, subject_hashes::text AS hashes
                    FROM ebay_account_deletion_notification
                    WHERE notification_id = %s
                    """,
                    (notification_id,),
                )
                audit = await result.fetchone()
                assert audit["deleted_market_evidence_count"] == 1
                assert subject not in audit["hashes"]
            finally:
                await connection.execute("ROLLBACK")
    finally:
        await database.close()
