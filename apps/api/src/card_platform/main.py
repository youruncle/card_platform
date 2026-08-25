import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from card_platform.api import router
from card_platform.auth_api import router as auth_router
from card_platform.config import get_settings
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
    recognition_worker = None
    recognition_worker_task = None
    scanner_worker = None
    scanner_worker_task = None
    if get_settings().recognition_worker_enabled:
        recognition_worker = RecognitionBenchmarkWorker(database, get_settings())
        recognition_worker_task = asyncio.create_task(recognition_worker.run())
    if get_settings().valuation_worker_enabled:
        concurrency = max(1, get_settings().valuation_worker_concurrency)
        worker_tasks = [asyncio.create_task(worker.run()) for _ in range(concurrency)]
    if get_settings().scanner_worker_enabled:
        scanner_worker = ScannerRecognitionWorker(database, get_settings())
        scanner_worker_task = asyncio.create_task(scanner_worker.run())
    try:
        yield
    finally:
        if worker_tasks:
            await stop_worker(worker_tasks, worker)
        if recognition_worker_task is not None and recognition_worker is not None:
            await stop_recognition_worker(recognition_worker_task, recognition_worker)
        if scanner_worker_task is not None and scanner_worker is not None:
            await stop_scanner_worker(scanner_worker_task, scanner_worker)
        await database.close()


app = FastAPI(title="Card Platform API", version="0.2.0", lifespan=lifespan)
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
    return await call_next(request)


app.include_router(router)
app.include_router(auth_router)
app.include_router(scanner_router)
app.include_router(flatbed_router)
app.include_router(identity_correction_router)
app.include_router(identity_review_router)
app.include_router(knowledge_router)
app.include_router(market_data_router)
app.include_router(recognition_benchmark_router)
app.include_router(ebay_notification_router)
app.mount(
    "/uploads",
    StaticFiles(directory=get_settings().resolved_upload_dir, check_dir=False),
    name="uploads",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness(request: Request) -> dict[str, str]:
    async with request.app.state.database.connection() as connection:
        await connection.execute("SELECT 1")
    return {"status": "ready"}
