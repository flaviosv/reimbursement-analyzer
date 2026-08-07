from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str = "ok"


class SampleMessage(BaseModel):
    id: str
    payload: str
