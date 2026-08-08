from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from dotenv import load_dotenv
from fastapi import FastAPI
from shared.config import load_config
from shared.db import managed_pool
from shared.models import HealthStatus
from shared.producer import managed_producer

from errors import register_handlers
from reimbursement.create.route import router as reimbursement_router

load_dotenv()


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


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
