import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from card_platform.api import router
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.identity_correction_api import router as identity_correction_router
from card_platform.knowledge_api import router as knowledge_router
from card_platform.knowledge_runtime import KnowledgeRuntime
from card_platform.market_data.api import router as market_data_router
from card_platform.recognition_benchmark_api import router as recognition_benchmark_router
from card_platform.recognition_benchmark_worker import (
    RecognitionBenchmarkWorker,
    stop_recognition_worker,
)
from card_platform.valuation_worker import AutomatedValuationWorker, stop_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = Database(get_settings())
    await database.open()
    app.state.database = database
    knowledge_runtime = KnowledgeRuntime(database)
    await knowledge_runtime.refresh()
    app.state.knowledge_runtime = knowledge_runtime
    worker = AutomatedValuationWorker(database, get_settings(), knowledge_runtime)
    worker_task = None
    recognition_worker = None
    recognition_worker_task = None
    if get_settings().recognition_worker_enabled:
        recognition_worker = RecognitionBenchmarkWorker(database, get_settings())
        recognition_worker_task = asyncio.create_task(recognition_worker.run())
    if get_settings().valuation_worker_enabled:
        worker_task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        if worker_task is not None:
            await stop_worker(worker_task, worker)
        if recognition_worker_task is not None and recognition_worker is not None:
            await stop_recognition_worker(recognition_worker_task, recognition_worker)
        await database.close()


app = FastAPI(title="Card Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(identity_correction_router)
app.include_router(knowledge_router)
app.include_router(market_data_router)
app.include_router(recognition_benchmark_router)
app.mount("/uploads", StaticFiles(directory=get_settings().upload_dir, check_dir=False), name="uploads")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness(request: Request) -> dict[str, str]:
    async with request.app.state.database.connection() as connection:
        await connection.execute("SELECT 1")
    return {"status": "ready"}
