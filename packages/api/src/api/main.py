import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from dotenv import load_dotenv
from fastapi import FastAPI
from shared.config import load_config
from shared.db import managed_pool
from shared.models import HealthStatus
from shared.producer import managed_producer

from api.errors import register_handlers
from api.reimbursement.create.route import router as reimbursement_router
from api.reimbursement.get.route import router as get_reimbursement_router
from api.reimbursement.list.route import router as list_reimbursement_router
from api.reimbursement.update.route import router as update_reimbursement_router

load_dotenv()
# Mirrors publisher/consumer.py's and reimbursement/consumer.py's own
# entrypoints (both call this on their own startup) — uvicorn's default
# logging setup only configures its own uvicorn/uvicorn.error/uvicorn.access
# loggers, never the root logger, so without this every new .info() call
# this feature adds would silently never emit.
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Owns every resource the app needs for its lifetime — the Kafka
    producer and the DB pool. New resources get added here as another
    `async with` / `app.state.*` assignment."""
    config = load_config()
    async with managed_producer(config.kafka.to_producer_config()) as producer:
        app.state.producer = producer
        async with managed_pool(replace(config.database, pool_min_size=0)) as pool:
            app.state.pool = pool
            yield


app = FastAPI(lifespan=lifespan)
register_handlers(app)
app.include_router(reimbursement_router)
app.include_router(list_reimbursement_router)
app.include_router(get_reimbursement_router)
app.include_router(update_reimbursement_router)


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
