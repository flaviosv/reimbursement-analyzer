from dotenv import load_dotenv
from fastapi import FastAPI
from shared.models import HealthStatus

load_dotenv()

app = FastAPI()


@app.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus()
