import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from card_platform.player_prices.api import router as player_prices_router
from card_platform.player_prices.service import run_price_worker
from card_platform.player_prices.image_worker import run_image_worker
from card_platform.purchase_cost_api import router as purchase_cost_router
from card_platform.cardhobby_account_api import router as cardhobby_account_router
from card_platform.purchase_cost_service import run_cost_worker
from card_platform.api import router
from card_platform.auth_api import router as auth_router
from card_platform.mobile_api import router as mobile_router
from card_platform.capture_api import router as capture_router
from card_platform.capture_media_api import router as capture_media_router
from card_platform.private_uploads import router as private_uploads_router
from card_platform.config import get_settings
from card_platform.codex_valuation_fallback import run_codex_fallback_worker
from card_platform.db import Database
from card_platform.identity_correction_api import router as identity_correction_router
from card_platform.identity_review_api import router as identity_review_router
from card_platform.knowledge_api import router as knowledge_router
from card_platform.knowledge_runtime import KnowledgeRuntime
from card_platform.market_data.api import router as market_data_router
from card_platform.recognition_benchmark_api import router as recognition_benchmark_router
from card_platform.recognition_benchmark_worker import (
    RecognitionBenchmarkWorker,
    stop_recognition_worker,
)
from card_platform.valuation_worker import AutomatedValuationWorker, stop_worker
from card_platform.scanner_api import router as scanner_router
from card_platform.scanner_queue import ScannerRecognitionWorker, stop_scanner_worker
from card_platform.flatbed_api import router as flatbed_router
from card_platform.ebay_notification_api import router as ebay_notification_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = Database(get_settings())
    await database.open()
    app.state.database = database
    knowledge_runtime = KnowledgeRuntime(database)
    await knowledge_runtime.refresh()
    app.state.knowledge_runtime = knowledge_runtime
    worker = AutomatedValuationWorker(database, get_settings(), knowledge_runtime)
    worker_tasks: list[asyncio.Task] = []
    codex_fallback_task = None
    recognition_worker = None
    recognition_worker_task = None
    scanner_worker = None
    scanner_worker_tasks: list[asyncio.Task] = []
    if get_settings().recognition_worker_enabled:
        recognition_worker = RecognitionBenchmarkWorker(database, get_settings())
        recognition_worker_task = asyncio.create_task(recognition_worker.run())
    if get_settings().valuation_worker_enabled:
        codex_fallback_task = asyncio.create_task(
            run_codex_fallback_worker(database, get_settings().resolved_upload_dir)
        )
        concurrency = max(1, get_settings().valuation_worker_concurrency)
        worker_tasks = [asyncio.create_task(worker.run()) for _ in range(concurrency)]
    if get_settings().scanner_worker_enabled:
        scanner_worker = ScannerRecognitionWorker(database, get_settings())
        concurrency = max(1, get_settings().scanner_worker_concurrency)
        scanner_worker_tasks = [
            asyncio.create_task(scanner_worker.run()) for _ in range(concurrency)
        ]
    cost_worker = (
        asyncio.create_task(run_cost_worker(database))
        if get_settings().purchase_cost_worker_enabled
        else None
    )
    price_worker = (asyncio.create_task(run_price_worker(database))
                    if get_settings().player_price_worker_enabled else None)
    image_worker = asyncio.create_task(run_image_worker(database, get_settings()))
    try:
        yield
    finally:
        image_worker.cancel()
        await asyncio.gather(image_worker, return_exceptions=True)
        if price_worker is not None:
            price_worker.cancel()
            await asyncio.gather(price_worker, return_exceptions=True)
        if codex_fallback_task is not None:
            codex_fallback_task.cancel()
            await asyncio.gather(codex_fallback_task, return_exceptions=True)
        if cost_worker is not None:
            cost_worker.cancel()
            await asyncio.gather(cost_worker, return_exceptions=True)
        if worker_tasks:
            await stop_worker(worker_tasks, worker)
        if recognition_worker_task is not None and recognition_worker is not None:
            await stop_recognition_worker(recognition_worker_task, recognition_worker)
        if scanner_worker_tasks and scanner_worker is not None:
            await stop_scanner_worker(scanner_worker_tasks, scanner_worker)
        await database.close()


app = FastAPI(title="Card Platform API", version="0.6.6", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_cross_site_session_writes(request: Request, call_next):
    settings = get_settings()
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.cookies.get(settings.auth_cookie_name)
    ):
        origin = request.headers.get("origin")
        if origin and origin not in settings.cors_origins:
            return JSONResponse(status_code=403, content={"detail": "请求来源未获授权"})
    response = await call_next(request)
    path = request.url.path
    if request.method in {"GET", "HEAD"} and (
        path.startswith("/v1/capture/")
        or (path.startswith("/v1/portfolio/items/") and "/images/" in path)
    ):
        # Include authorization and validation errors in the private media policy.
        response.headers["Cache-Control"] = "private, no-store"
        response.headers.add_vary_header("Cookie, Authorization")
    return response


app.include_router(router)
app.include_router(player_prices_router)
app.include_router(purchase_cost_router)
app.include_router(cardhobby_account_router)
app.include_router(auth_router)
app.include_router(mobile_router)
app.include_router(capture_router)
app.include_router(capture_media_router)
app.include_router(scanner_router)
app.include_router(flatbed_router)
app.include_router(identity_correction_router)
app.include_router(identity_review_router)
app.include_router(knowledge_router)
app.include_router(market_data_router)
app.include_router(recognition_benchmark_router)
app.include_router(ebay_notification_router)
app.include_router(private_uploads_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness(request: Request) -> dict[str, str]:
    async with request.app.state.database.connection() as connection:
        await connection.execute("SELECT 1")
    return {"status": "ready"}


@app.get("/v1/client-capabilities", tags=["system"])
def client_capabilities() -> dict:
    return {"version": 1, "capture_jobs": True, "single_side": True,
            "supplement": True, "crop_proposals": True,
            "image_variants": True, "portfolio_sort": True, "pair_integrity": True,
            "pair_review_version": 1, "flatbed_source_confirmation": True}
