from confluent_kafka.aio import AIOProducer
from fastapi import APIRouter, Depends, Request

from api.kafka import get_producer
from api.reimbursement.create.producer import publish
from api.reimbursement.create.validation import BATCH_ADAPTER, read_capped, validate_batch
from api.responses import MessageResponse

router = APIRouter()


@router.post(
    "/api/v1/reimbursement",
    status_code=201,
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse, "description": "Batch failed validation"},
        413: {"model": MessageResponse, "description": "Body exceeds the 25 MiB ceiling"},
        500: {"model": MessageResponse, "description": "Failed to publish the batch"},
    },
    # Derived from the same adapter that validates the body, so the
    # documented schema and the enforced one cannot drift apart.
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"schema": BATCH_ADAPTER.json_schema()}},
            "required": True,
        }
    },
)
async def create_reimbursement(
    request: Request, producer: AIOProducer = Depends(get_producer)
) -> MessageResponse:
    """Orchestrates cap -> validate -> publish -> respond. No logic of its
    own — every failure mode is a raise from one of the three collaborators,
    caught by the app-wide handlers registered in errors.py."""
    raw = await read_capped(request)
    batch = validate_batch(raw)
    request_ids = [item.request_id for item in batch]
    await publish(producer, raw, request_ids)
    return MessageResponse(msg=f"{len(batch)} request(s) accepted")
