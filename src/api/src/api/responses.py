from pydantic import BaseModel


class MessageResponse(BaseModel):
    """The api's single response-body shape for every non-2xx status, and for
    201. api-only by definition — no other service builds HTTP responses, so
    this stays out of `shared`."""

    msg: str
