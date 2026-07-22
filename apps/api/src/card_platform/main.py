import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from card_platform.api import router
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.valuation_worker import AutomatedValuationWorker, stop_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = Database(get_settings())
    await database.open()
    app.state.database = database
    worker = AutomatedValuationWorker(database, get_settings())
    worker_task = None
    if get_settings().valuation_worker_enabled:
        worker_task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        if worker_task is not None:
            await stop_worker(worker_task, worker)
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
app.mount("/uploads", StaticFiles(directory=get_settings().upload_dir, check_dir=False), name="uploads")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def readiness(request: Request) -> dict[str, str]:
    async with request.app.state.database.connection() as connection:
        await connection.execute("SELECT 1")
    return {"status": "ready"}
