from dotenv import load_dotenv
from fastapi import FastAPI
from shared.models import HealthStatus

from errors import register_handlers
from kafka import lifespan_producer
from reimbursement.create.route import router as reimbursement_router

load_dotenv()

app = FastAPI(lifespan=lifespan_producer)
register_handlers(app)
app.include_router(reimbursement_router)


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
