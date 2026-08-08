from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from shared.config import load_config
from shared.models import HealthStatus
from shared.producer import managed_producer

from errors import register_handlers
from reimbursement.create.route import router as reimbursement_router

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Owns every resource the app needs for its lifetime — currently just
    the Kafka producer. New resources (a DB pool, a second client, ...) get
    added here as another `async with` / `app.state.*` assignment."""
    kafka_config = load_config().kafka
    async with managed_producer(kafka_config.to_producer_config()) as producer:
        app.state.producer = producer
        yield


app = FastAPI(lifespan=lifespan)
register_handlers(app)
app.include_router(reimbursement_router)


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
