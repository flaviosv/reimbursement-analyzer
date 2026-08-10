"""E2E retry-ceiling / ghost / stale handling — real chain, no LLM
involved (E2E-07, E2E-08, E2E-09).

Each case hand-crafts a `Reimbursement` envelope straight onto the real
Kafka topic (bypassing the publisher) — validation.py's decision tree
short-circuits all three branches before the decision graph ever runs, so
none of them costs a real Groq call on their own. The retry-ceiling and
stale cases still need one *real* item (created via a real POST, per
design.md's "no direct DB access from test code" decision) to attach their
crafted envelope to — that item's own natural decision is what costs the
Groq call, not the crafted envelope.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from payload_builders import approve_bucket_payload
from polling import find_uuid_by_request_id, wait_for_status
from shared.config import REIMBURSEMENT_TOPIC, KafkaConfig
from shared.models import ReimbursementEnvelope
from shared.producer import managed_producer, publish

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

_FIND_UUID_TIMEOUT_SECONDS = 30.0
_NATURAL_DECISION_TIMEOUT_SECONDS = 90.0
_ESCALATE_TIMEOUT_SECONDS = 30.0
# No Groq call is involved in any of these 3 branches (validation.py
# short-circuits before the decision graph), so this only needs to be long
# enough for one Kafka round trip + a DB read/write — not a bounded LLM
# wait like the other timeouts above.
_SETTLE_SECONDS = 5.0

_NATURAL_TERMINAL_STATUSES = {"auto-approved", "auto-rejected", "human-review"}


async def _produce_envelope(kafka_producer_config: KafkaConfig, envelope: ReimbursementEnvelope) -> None:
    async with managed_producer(kafka_producer_config.to_producer_config()) as producer:
        await publish(
            producer,
            REIMBURSEMENT_TOPIC,
            envelope.model_dump_json().encode(),
            kafka_producer_config.publish_timeout_seconds,
        )


def _post_real_item(api_client: httpx.Client, request_id: str) -> UUID:
    response = api_client.post("/api/v1/reimbursement", json=[approve_bucket_payload(request_id)])
    assert response.status_code == 201
    return find_uuid_by_request_id(api_client, request_id, timeout=_FIND_UUID_TIMEOUT_SECONDS)


class DescribeTheNonDecisionBranches:
    async def it_escalates_a_row_at_the_retry_ceiling_to_human_review_with_no_groq_call(
        self, api_client: httpx.Client, kafka_producer_config: KafkaConfig
    ) -> None:
        uuid = _post_real_item(api_client, f"E2E-RETRYCEILING-{uuid4().hex[:12]}")
        # Let the item's own natural (real-Groq) decision land first, so the
        # human-review this test asserts on is unambiguously the escalate
        # branch's own write, not a race against the natural decision's own
        # Reimbursement(retry=0) message landing on human-review by chance.
        wait_for_status(api_client, uuid, _NATURAL_TERMINAL_STATUSES, timeout=_NATURAL_DECISION_TIMEOUT_SECONDS)

        envelope = ReimbursementEnvelope(uuid=uuid, retry=4, published_at=datetime.now(UTC))
        await _produce_envelope(kafka_producer_config, envelope)

        # MAX_RETRY=3: validation.handle_message's `retry > MAX_RETRY` check
        # routes straight to _escalate(), which never touches agent.decide()
        # (the only place a real Groq call happens) — no LLM call here.
        item = wait_for_status(api_client, uuid, {"human-review"}, timeout=_ESCALATE_TIMEOUT_SECONDS)
        assert item["decision_reason"] is not None

    async def it_drops_a_ghost_message_with_no_row_created(
        self, api_client: httpx.Client, kafka_producer_config: KafkaConfig
    ) -> None:
        ghost_uuid = uuid4()  # never POSTed — no row exists for this uuid
        envelope = ReimbursementEnvelope(uuid=ghost_uuid, retry=0, published_at=datetime.now(UTC))
        await _produce_envelope(kafka_producer_config, envelope)
        await asyncio.sleep(_SETTLE_SECONDS)

        response = api_client.get(f"/api/v1/reimbursement/{ghost_uuid}")
        assert response.status_code == 404

        # "No crash" (the rest of E2E-08's AC) has no direct HTTP-observable
        # signal of its own — validation._resolve's ghost branch returns
        # silently, it does not write anything a GET could catch. Per
        # design.md/tasks.md, the proxy is the next real-chain test in this
        # file (below) still passing: if the ghost message had wedged or
        # crashed the same long-running consumer loop, that next test's own
        # wait_for_status would time out.

    async def it_ignores_a_stale_message_and_leaves_the_settled_decision_untouched(
        self, api_client: httpx.Client, kafka_producer_config: KafkaConfig
    ) -> None:
        # SPEC_DEVIATION from spec.md's E2E-09 AC3 ("row SHALL remain
        # status=pending with a null decision_reason"): that wording fits
        # test_integration.py's single-hop test, whose row is inserted
        # directly with no competing message ever produced for it. A row
        # created via a real POST (this suite's own "no direct DB access"
        # rule, design.md's Tech Decisions) always has its own natural
        # Reimbursement(retry=0) message racing to decide it — nothing this
        # test does can keep that row "pending" forever, and racing to grab
        # it before that natural decision lands would make this test's
        # result depend on Groq latency, which the project's own strictness
        # policy treats as a signal worth investigating, not noise to
        # tolerate. Reason: the equivalent, race-free proof against the
        # real stack is that a message older than the row's *settled*
        # updated_at is still a no-op — the same guard
        # (`envelope.published_at < row["updated_at"]`), exercised on a
        # decided row instead of a pending one, which is also the realistic
        # scenario the guard defends against (a late/duplicate message
        # arriving after a newer one already decided the row).
        uuid = _post_real_item(api_client, f"E2E-STALE-{uuid4().hex[:12]}")
        settled = wait_for_status(
            api_client, uuid, _NATURAL_TERMINAL_STATUSES, timeout=_NATURAL_DECISION_TIMEOUT_SECONDS
        )

        stale_published_at = datetime.fromisoformat(settled["updated_at"]) - timedelta(hours=1)
        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=stale_published_at)
        await _produce_envelope(kafka_producer_config, envelope)
        await asyncio.sleep(_SETTLE_SECONDS)

        response = api_client.get(f"/api/v1/reimbursement/{uuid}")
        assert response.status_code == 200
        item = response.json()["data"]
        assert item["status"] == settled["status"]
        assert item["decision_reason"] == settled["decision_reason"]
