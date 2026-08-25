from uuid import UUID

from psycopg import AsyncConnection

from card_platform.repository import NotFoundError
from card_platform.schemas import (
    ExpertAnalysisHistory,
    ExpertAnalysisJobView,
    ExpertAnalysisSnapshot,
)


class ExpertAnalysisRepository:
    """Read externally-produced Codex multimodal snapshots only."""

    async def history(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> ExpertAnalysisHistory:
        owned = await connection.execute(
            "SELECT 1 FROM collection_item WHERE id=%s AND owner_id=%s AND deleted_at IS NULL",
            (item_id, owner_id),
        )
        if await owned.fetchone() is None:
            raise NotFoundError("collection item not found")
        result = await connection.execute(
            """SELECT s.* FROM card_analysis_snapshot s
               JOIN card_analysis_job j ON j.id=s.job_id
               WHERE s.collection_item_id=%s AND j.owner_id=%s
                 AND s.source='codex_multimodal'
               ORDER BY s.analysis_version DESC""",
            (item_id, owner_id),
        )
        snapshots = [ExpertAnalysisSnapshot(**dict(row)) for row in await result.fetchall()]
        job_result = await connection.execute(
            """SELECT id, collection_item_id, status, attempt_count, error_message,
                      created_at, started_at, finished_at
               FROM card_analysis_job WHERE collection_item_id=%s AND owner_id=%s
                 AND requested_source='codex_multimodal'
               ORDER BY created_at DESC LIMIT 1""",
            (item_id, owner_id),
        )
        job = await job_result.fetchone()
        latest = next((entry for entry in snapshots if entry.status == "succeeded"), None)
        return ExpertAnalysisHistory(
            latest=latest,
            history=snapshots,
            current_job=ExpertAnalysisJobView(**dict(job)) if job else None,
        )
