from fastapi import FastAPI
from shared.models import HealthStatus

app = FastAPI()


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
