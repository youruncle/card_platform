#!/usr/bin/env python3
"""Import historical successful scanner recognitions that an old quality gate hid."""

from __future__ import annotations

import asyncio

import aiofiles
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.db import Database
from card_platform.image_service import ImageStorageService
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.repository import PortfolioRepository
from card_platform.scanner_queue import ScannerQueueService, _auto_import_payload
from card_platform.schemas import CardRecognitionResult
from card_platform.vision_routing import finalize_recognition_status


async def main() -> None:
    settings = Settings()
    database = Database(settings)
    await database.open()
    imported = 0
    skipped = 0
    try:
        async with database.connection() as connection:
            rows = await connection.execute(
                """SELECT * FROM scanner_recognition_job
                   WHERE status = 'succeeded'
                     AND collection_item_id IS NULL
                     AND result IS NOT NULL
                   ORDER BY created_at, id"""
            )
            jobs = [dict(row) for row in await rows.fetchall()]

        queue = ScannerQueueService(settings)
        storage = ImageStorageService(settings)
        repository = PortfolioRepository()
        for job in jobs:
            try:
                result = finalize_recognition_status(
                    canonicalize_identity(
                        CardRecognitionResult.model_validate(job["result"])
                    )
                )
                front_path = queue.image_path(job["front_image_path"])
                back_path = queue.image_path(job["back_image_path"])
                async with aiofiles.open(front_path, "rb") as source:
                    front_content = await source.read()
                async with aiofiles.open(back_path, "rb") as source:
                    back_content = await source.read()

                async with database.connection() as connection, connection.transaction():
                    locked = await connection.execute(
                        """SELECT collection_item_id
                           FROM scanner_recognition_job
                           WHERE id = %s FOR UPDATE""",
                        (job["id"],),
                    )
                    current = await locked.fetchone()
                    if current is None or current["collection_item_id"] is not None:
                        skipped += 1
                        continue
                    item_id = await repository.create_item(
                        connection, _auto_import_payload(job["owner_id"], result)
                    )
                    await storage.save_preprocessed_item_image_content(
                        connection,
                        item_id,
                        job["owner_id"],
                        "front",
                        front_content,
                        "image/jpeg",
                    )
                    await storage.save_preprocessed_item_image_content(
                        connection,
                        item_id,
                        job["owner_id"],
                        "back",
                        back_content,
                        "image/jpeg",
                    )
                    await connection.execute(
                        """UPDATE scanner_recognition_job
                           SET result = %s, collection_item_id = %s,
                               imported_at = now(), updated_at = now(),
                               error_message = NULL
                           WHERE id = %s""",
                        (Jsonb(result.model_dump(mode="json")), item_id, job["id"]),
                    )
                imported += 1
            except Exception as error:
                skipped += 1
                print(f"SKIP {job['id']}: {error}")
        print(f"Imported {imported}; skipped {skipped}; total {len(jobs)}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
