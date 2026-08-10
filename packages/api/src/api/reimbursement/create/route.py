import logging

from confluent_kafka.aio import AIOProducer
from fastapi import APIRouter, Depends, Request
from shared.config import MAX_BODY_BYTES, load_config
from shared.logging import log_event

from api.dependencies import get_producer
from api.errors import MessageResponse
from api.reimbursement.create.payload import read_capped
from api.reimbursement.create.producer import publish
from api.reimbursement.create.validation import BATCH_ADAPTER, validate_batch

logger = logging.getLogger(__name__)
router = APIRouter()

BATCH_ACCEPTED_EVENT = "reimbursement.batch_accepted"


@router.post(
    "/api/v1/reimbursement",
    status_code=201,
    response_model=MessageResponse,
    responses={
        400: {"model": MessageResponse, "description": "Batch failed validation"},
        413: {
            "model": MessageResponse,
            "description": f"Body exceeds the {MAX_BODY_BYTES // (1024 * 1024)} MiB ceiling",
        },
        500: {"model": MessageResponse, "description": "Failed to publish the batch"},
    },
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
    """Orchestrates cap -> validate -> publish -> respond, extracting the
    request IDs the response and error logging both need along the way —
    every failure mode is still a raise from one of the three collaborators,
    caught by the app-wide handlers registered in errors.py."""
    raw = await read_capped(request)
    batch = validate_batch(raw)
    request_ids = [item.request_id for item in batch]
    accepted_count = len(batch)
    # The parsed model graph is only needed for request_ids/accepted_count
    # above; dropping it here keeps it from staying alive (alongside raw and
    # the envelope publish() builds from it) for the duration of the publish
    # call, which is where peak memory actually matters.
    del batch
    await publish(producer, raw, request_ids, load_config().kafka)
    log_event(
        logger,
        logging.INFO,
        BATCH_ACCEPTED_EVENT,
        request_ids=request_ids,
        accepted_count=accepted_count,
    )
    return MessageResponse(msg=f"{accepted_count} request(s) accepted")
