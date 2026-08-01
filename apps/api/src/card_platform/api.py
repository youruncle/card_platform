from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.analysis_repository import AnalysisRepository
from card_platform.ai_service import AIAdvisoryService
from card_platform.card_search_service import CardSearchService
from card_platform.cardhobby_import import CardHobbyImportService
from card_platform.config import get_settings
from card_platform.intelligence_repository import IntelligenceRepository
from card_platform.ingestion.service import PlayerSyncService
from card_platform.image_service import (
    CardVisionService,
    ImageStorageService,
    InvalidImageError,
)
from card_platform.market_repository import MarketRepository
from card_platform.repository import (
    ConflictError,
    NotFoundError,
    PortfolioRepository,
    PositionError,
)
from card_platform.schemas import (
    CardAnalysisCreate,
    CardAnalysisResult,
    AINarrativeResult,
    AutomatedValuationDetail,
    AutoCardAnalysisCreate,
    CollectionItemCreate,
    CollectionItemDelete,
    CollectionItemOwnerAction,
    CreatedResource,
    PortfolioItem,
    PortfolioItemDetail,
    PortfolioSummary,
    PlayerEventCreate,
    PlayerIntelligence,
    PlayerMetricUpsert,
    PlayerPotential,
    PlayerSyncRequest,
    PlayerSyncResult,
    MarketCompCreate,
    MarketCompView,
    MarketEstimateCreate,
    MarketEstimateResult,
    CardRecognitionResult,
    CardHobbyImportConfirmRequest,
    CardHobbyImportConfirmResult,
    CardHobbyImportPreviewRequest,
    CardHobbyImportPreviewResult,
    BatchCardRecognitionItem,
    SheetCardRecognitionResult,
    ImageUploadResult,
    PotentialInput,
    TransactionCreate,
    UserCreate,
    ValuationCreate,
)

router = APIRouter(prefix="/v1")
repository = PortfolioRepository()
analysis_repository = AnalysisRepository()
intelligence_repository = IntelligenceRepository()
market_repository = MarketRepository()


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.database.connection() as connection:
        yield connection


def translate_domain_error(error: Exception) -> HTTPException:
    if isinstance(error, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, PositionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/users", response_model=CreatedResource, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    user_id = await repository.create_user(connection, payload)
    return CreatedResource(id=user_id)


@router.post(
    "/portfolio/items", response_model=CreatedResource, status_code=status.HTTP_201_CREATED
)
async def create_item(
    payload: CollectionItemCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        item_id = await repository.create_item(connection, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    return CreatedResource(id=item_id)


@router.post(
    "/integrations/cardhobby/import-preview",
    response_model=CardHobbyImportPreviewResult,
    status_code=status.HTTP_201_CREATED,
)
async def preview_cardhobby_import(
    payload: CardHobbyImportPreviewRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> CardHobbyImportPreviewResult:
    try:
        return await CardHobbyImportService(get_settings(), repository).preview(
            connection, payload
        )
    except (NotFoundError, ConflictError) as error:
        raise translate_domain_error(error) from error


@router.post(
    "/integrations/cardhobby/import-batches/{batch_id}/confirm",
    response_model=CardHobbyImportConfirmResult,
)
async def confirm_cardhobby_import(
    batch_id: UUID,
    payload: CardHobbyImportConfirmRequest,
    connection: AsyncConnection = Depends(get_connection),
) -> CardHobbyImportConfirmResult:
    try:
        imported, skipped = await CardHobbyImportService(
            get_settings(), repository
        ).confirm(connection, batch_id, payload.owner_id, payload.items)
    except (NotFoundError, ConflictError) as error:
        raise translate_domain_error(error) from error
    return CardHobbyImportConfirmResult(
        imported_item_ids=imported,
        skipped_draft_ids=skipped,
    )


@router.get("/portfolio/items", response_model=list[PortfolioItem])
async def list_items(
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[PortfolioItem]:
    return await repository.list_items(connection, owner_id)


@router.get("/portfolio/trash", response_model=list[PortfolioItem])
async def list_deleted_items(
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[PortfolioItem]:
    return await repository.list_deleted_items(connection, owner_id)


@router.delete("/portfolio/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: UUID,
    payload: CollectionItemDelete,
    connection: AsyncConnection = Depends(get_connection),
) -> None:
    try:
        await repository.soft_delete_item(connection, item_id, payload.owner_id, payload.reason)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/portfolio/items/{item_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def restore_item(
    item_id: UUID,
    payload: CollectionItemOwnerAction,
    connection: AsyncConnection = Depends(get_connection),
) -> None:
    try:
        await repository.restore_item(connection, item_id, payload.owner_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.delete(
    "/portfolio/items/{item_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def permanently_delete_item(
    item_id: UUID,
    payload: CollectionItemOwnerAction,
    connection: AsyncConnection = Depends(get_connection),
) -> None:
    try:
        image_urls = await repository.permanently_delete_item(
            connection, item_id, payload.owner_id
        )
    except (NotFoundError, ConflictError) as error:
        raise translate_domain_error(error) from error
    ImageStorageService(get_settings()).delete_item_images(item_id, image_urls)


@router.get("/portfolio/items/{item_id}/detail", response_model=PortfolioItemDetail)
async def get_item_detail(
    item_id: UUID,
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> PortfolioItemDetail:
    try:
        return await repository.item_detail(connection, owner_id, item_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.get(
    "/portfolio/items/{item_id}/catalog-enrichment",
    response_model=CardRecognitionResult,
)
async def enrich_item_catalog(
    item_id: UUID,
    owner_id: UUID,
    force_refresh: bool = False,
    connection: AsyncConnection = Depends(get_connection),
) -> CardRecognitionResult:
    try:
        detail = await repository.item_detail(connection, owner_id, item_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    if not force_refresh:
        cached_result = await connection.execute(
            """
            SELECT result
            FROM card_catalog_snapshot
            WHERE collection_item_id = %s
            """,
            (item_id,),
        )
        cached = await cached_result.fetchone()
        if cached:
            return CardRecognitionResult.model_validate(cached["result"])
    item = detail.item
    context = "；".join(
        row.notes for row in detail.transactions if row.notes
    ) or None
    identity = CardRecognitionResult(
        player_name=item.player_name,
        season_start=item.season_start,
        season_end=item.season_end,
        brand_name=item.brand_name,
        product_name=item.product_name,
        insert_name=item.insert_name,
        parallel_name=item.parallel_name,
        card_number=item.card_number,
        serial_number=item.serial_number,
        serial_total=item.serial_total,
        is_rookie=item.is_rookie,
        is_autograph=item.is_autograph,
        is_patch=item.is_patch,
        confidence=1,
    )
    enriched = await CardSearchService(get_settings()).enrich(identity, context=context)
    await connection.execute(
        """
        INSERT INTO card_catalog_snapshot (
            collection_item_id, result, verified_at, updated_at
        ) VALUES (%s, %s, now(), now())
        ON CONFLICT (collection_item_id) DO UPDATE SET
            result = EXCLUDED.result,
            verified_at = now(),
            updated_at = now()
        """,
        (item_id, Jsonb(enriched.model_dump(mode="json"))),
    )
    return enriched


@router.get("/portfolio/summary", response_model=PortfolioSummary)
async def portfolio_summary(
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> PortfolioSummary:
    try:
        return await repository.summary(connection, owner_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/portfolio/items/{item_id}/transactions",
    response_model=CreatedResource,
    status_code=status.HTTP_201_CREATED,
)
async def add_transaction(
    item_id: UUID,
    owner_id: UUID,
    payload: TransactionCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        transaction_id = await repository.add_transaction(
            connection, item_id, owner_id, payload
        )
    except (NotFoundError, PositionError) as error:
        raise translate_domain_error(error) from error
    return CreatedResource(id=transaction_id)


@router.post(
    "/portfolio/items/{item_id}/valuations",
    response_model=CreatedResource,
    status_code=status.HTTP_201_CREATED,
)
async def add_valuation(
    item_id: UUID,
    owner_id: UUID,
    payload: ValuationCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        valuation_id = await repository.add_valuation(connection, item_id, owner_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    return CreatedResource(id=valuation_id)


@router.post(
    "/portfolio/items/{item_id}/market-comps",
    response_model=CreatedResource,
    status_code=status.HTTP_201_CREATED,
)
async def create_market_comp(
    item_id: UUID,
    payload: MarketCompCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        comp_id = await market_repository.create_comp(connection, item_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return CreatedResource(id=comp_id)


@router.get("/portfolio/items/{item_id}/market-comps", response_model=list[MarketCompView])
async def list_market_comps(
    item_id: UUID,
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[MarketCompView]:
    try:
        return await market_repository.list_comps(connection, item_id, owner_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/portfolio/items/{item_id}/market-estimate",
    response_model=MarketEstimateResult,
)
async def create_market_estimate(
    item_id: UUID,
    payload: MarketEstimateCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> MarketEstimateResult:
    try:
        return await market_repository.estimate(connection, item_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.get(
    "/portfolio/items/{item_id}/automated-valuation",
    response_model=AutomatedValuationDetail,
)
async def get_automated_valuation(
    item_id: UUID,
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> AutomatedValuationDetail:
    try:
        return await market_repository.automated_detail(connection, item_id, owner_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/portfolio/items/{item_id}/automated-valuation/retry",
    response_model=CreatedResource,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_automated_valuation(
    item_id: UUID,
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        job_id = await market_repository.enqueue_valuation(connection, item_id, owner_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    return CreatedResource(id=job_id)


@router.post(
    "/portfolio/items/{item_id}/images",
    response_model=ImageUploadResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_item_image(
    item_id: UUID,
    owner_id: UUID,
    side: str,
    image: UploadFile = File(...),
    connection: AsyncConnection = Depends(get_connection),
) -> ImageUploadResult:
    try:
        return await ImageStorageService(get_settings()).save_item_image(
            connection, item_id, owner_id, side, image
        )
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    except InvalidImageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))


@router.post(
    "/portfolio/items/{item_id}/catalog-images/apply",
    response_model=list[ImageUploadResult],
)
async def apply_catalog_images(
    item_id: UUID,
    owner_id: UUID,
    side: str = "both",
    connection: AsyncConnection = Depends(get_connection),
) -> list[ImageUploadResult]:
    if side not in {"front", "back", "both"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="side must be front, back, or both",
        )
    try:
        await repository.item_detail(connection, owner_id, item_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    snapshot_result = await connection.execute(
        """
        SELECT result
        FROM card_catalog_snapshot
        WHERE collection_item_id = %s
        """,
        (item_id,),
    )
    snapshot = await snapshot_result.fetchone()
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先重新核验图鉴，确认高清参考图后再替换",
        )
    catalog = CardRecognitionResult.model_validate(snapshot["result"])
    requested = ("front", "back") if side == "both" else (side,)
    urls = {
        "front": catalog.reference_image_front_url,
        "back": catalog.reference_image_back_url,
    }
    missing = [image_side for image_side in requested if not urls[image_side]]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"图鉴未找到可信的{'、'.join(missing)}高清图",
        )
    storage = ImageStorageService(get_settings())
    try:
        async with connection.transaction():
            return [
                await storage.save_remote_item_image(
                    connection,
                    item_id,
                    owner_id,
                    image_side,
                    urls[image_side] or "",
                )
                for image_side in requested
            ]
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    except InvalidImageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.post("/cards/recognize", response_model=CardRecognitionResult)
async def recognize_card(image: UploadFile = File(...)) -> CardRecognitionResult:
    try:
        return await CardVisionService(get_settings()).recognize(image)
    except InvalidImageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"vision provider failed: {str(error)[:300]}",
        ) from error


@router.post("/cards/recognize-pair", response_model=CardRecognitionResult)
async def recognize_card_pair(
    front: UploadFile = File(...), back: UploadFile = File(...)
) -> CardRecognitionResult:
    try:
        return await CardVisionService(get_settings()).recognize_pair(front, back)
    except InvalidImageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"vision provider failed: {str(error)[:300]}",
        ) from error


@router.post("/cards/recognize-batch", response_model=list[BatchCardRecognitionItem])
async def recognize_card_batch(images: list[UploadFile] = File(...)) -> list[BatchCardRecognitionItem]:
    if not 1 <= len(images) <= 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="batch recognition accepts between 1 and 20 images",
        )
    service = CardVisionService(get_settings())
    results: list[BatchCardRecognitionItem] = []
    for image in images:
        try:
            result = await service.recognize(image)
            results.append(
                BatchCardRecognitionItem(filename=image.filename or "image", result=result)
            )
        except Exception as error:
            results.append(
                BatchCardRecognitionItem(filename=image.filename or "image", error=str(error))
            )
    return results


@router.post("/cards/recognize-auto", response_model=SheetCardRecognitionResult)
async def recognize_card_auto(image: UploadFile = File(...)) -> SheetCardRecognitionResult:
    """Auto-detect whether an image contains one card or many and parse each one."""
    try:
        return await CardVisionService(get_settings()).recognize_sheet(image)
    except InvalidImageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"vision provider failed: {str(error)[:300]}",
        ) from error


@router.post(
    "/portfolio/items/{item_id}/analysis",
    response_model=CardAnalysisResult,
    status_code=status.HTTP_201_CREATED,
)
async def analyze_item(
    item_id: UUID,
    payload: CardAnalysisCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CardAnalysisResult:
    try:
        return await analysis_repository.analyze_item(connection, item_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/portfolio/items/{item_id}/analysis/auto",
    response_model=CardAnalysisResult,
    status_code=status.HTTP_201_CREATED,
)
async def automatically_analyze_item(
    item_id: UUID,
    payload: AutoCardAnalysisCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CardAnalysisResult:
    try:
        return await analysis_repository.automatically_analyze_item(connection, item_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post("/analyses/{analysis_id}/narrative", response_model=AINarrativeResult)
async def generate_ai_narrative(
    analysis_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> AINarrativeResult:
    try:
        return await AIAdvisoryService(get_settings()).generate_and_save(connection, analysis_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.put("/players/{player_id}/metrics", status_code=status.HTTP_204_NO_CONTENT)
async def upsert_player_metric(
    player_id: UUID,
    payload: PlayerMetricUpsert,
    connection: AsyncConnection = Depends(get_connection),
) -> None:
    try:
        await intelligence_repository.upsert_metric(connection, player_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post(
    "/players/{player_id}/events",
    response_model=CreatedResource,
    status_code=status.HTTP_201_CREATED,
)
async def create_player_event(
    player_id: UUID,
    payload: PlayerEventCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        event_id = await intelligence_repository.create_event(connection, player_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
    return CreatedResource(id=event_id)


@router.post("/players/{player_id}/potential", response_model=PlayerPotential)
async def assess_player_potential(
    player_id: UUID,
    payload: PotentialInput,
    connection: AsyncConnection = Depends(get_connection),
) -> PlayerPotential:
    try:
        return await intelligence_repository.assess_potential(connection, player_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.get("/players/{player_id}/intelligence", response_model=PlayerIntelligence)
async def get_player_intelligence(
    player_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> PlayerIntelligence:
    try:
        return await intelligence_repository.get_intelligence(connection, player_id)
    except NotFoundError as error:
        raise translate_domain_error(error) from error


@router.post("/players/{player_id}/sync", response_model=PlayerSyncResult)
async def sync_player_data(
    player_id: UUID,
    payload: PlayerSyncRequest,
    request: Request,
    connection: AsyncConnection = Depends(get_connection),
) -> PlayerSyncResult:
    try:
        service = getattr(
            request.app.state,
            "player_sync_service",
            PlayerSyncService(get_settings()),
        )
        return await service.sync(connection, player_id, payload)
    except NotFoundError as error:
        raise translate_domain_error(error) from error
