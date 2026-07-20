from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from psycopg import AsyncConnection

from card_platform.analysis_repository import AnalysisRepository
from card_platform.ai_service import AIAdvisoryService
from card_platform.config import get_settings
from card_platform.intelligence_repository import IntelligenceRepository
from card_platform.ingestion.service import PlayerSyncService
from card_platform.image_service import (
    CardVisionService,
    ImageStorageService,
    InvalidImageError,
)
from card_platform.market_repository import MarketRepository
from card_platform.repository import NotFoundError, PortfolioRepository, PositionError
from card_platform.schemas import (
    CardAnalysisCreate,
    CardAnalysisResult,
    AINarrativeResult,
    AutoCardAnalysisCreate,
    CollectionItemCreate,
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


@router.get("/portfolio/items", response_model=list[PortfolioItem])
async def list_items(
    owner_id: UUID,
    connection: AsyncConnection = Depends(get_connection),
) -> list[PortfolioItem]:
    return await repository.list_items(connection, owner_id)


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
    payload: TransactionCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        transaction_id = await repository.add_transaction(connection, item_id, payload)
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
    payload: ValuationCreate,
    connection: AsyncConnection = Depends(get_connection),
) -> CreatedResource:
    try:
        valuation_id = await repository.add_valuation(connection, item_id, payload)
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


@router.post("/cards/recognize", response_model=CardRecognitionResult)
async def recognize_card(image: UploadFile = File(...)) -> CardRecognitionResult:
    try:
        return await CardVisionService(get_settings()).recognize(image)
    except InvalidImageError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))


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
