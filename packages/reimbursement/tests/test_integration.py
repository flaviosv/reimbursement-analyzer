import asyncio
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import reimbursement.agent.agent as agent_module
from agent_fakes import DEFAULT_TEST_MODEL_NAME, FakeStructuredModel
from confluent_kafka import KafkaException, TopicPartition
from reimbursement.agent.nodes.analysis import Analysis, GuardrailVerdict
from reimbursement.agent.nodes.apply_agent_decision import ApplyAgentDecision
from reimbursement.agent.nodes.apply_policies import ApplyPolicies
from reimbursement.agent.nodes.extract_fields import (
    ExtractedFieldsSchema,
    ExtractFields,
)
from reimbursement.agent.nodes.validate import Validate
from reimbursement.config import AgentConfig, load_agent_config
from reimbursement.consumer import managed_consumer, run
from reimbursement.validation import (
    DECIDED_EVENT,
    GHOST_DROPPED_EVENT,
    RESOLVED_EVENT,
    STALE_IGNORED_EVENT,
    Dependencies,
)
from shared.config import REIMBURSEMENT_TOPIC, Config, load_config
from shared.db import managed_pool
from shared.models import ReimbursementEnvelope
from shared.producer import managed_producer, publish
from shared.reimbursement.repository import insert_pending
from shared.reimbursement.use_cases.apply_decision import apply_decision
from shared.testing import valid_reimbursement_item

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# One group for the whole module, so each test's run picks up only the
# messages it produced: a fresh group per test would re-read every earlier
# test's message from the shared topic.
GROUP_ID = f"agent-integration-{time.monotonic_ns()}"


def _config() -> tuple[Config, AgentConfig]:
    return load_config(), replace(load_agent_config(), consumer_group_id=GROUP_ID)


async def _insert_row(migrated_db: str, request_id: str) -> UUID:
    connection = await asyncpg.connect(migrated_db)
    try:
        return await insert_pending(connection, valid_reimbursement_item(request_id))
    finally:
        await connection.close()


async def _row(migrated_db: str, uuid: UUID) -> asyncpg.Record:
    connection = await asyncpg.connect(migrated_db)
    try:
        return await connection.fetchrow("SELECT * FROM reimbursement WHERE uuid = $1", uuid)
    finally:
        await connection.close()


async def _produce(config: Config, envelope: ReimbursementEnvelope) -> None:
    async with managed_producer(config.kafka.to_producer_config()) as producer:
        await publish(
            producer,
            REIMBURSEMENT_TOPIC,
            envelope.model_dump_json().encode(),
            config.kafka.publish_timeout_seconds,
        )


async def _committed_offset(kafka_consumer: Any, timeout: float = 30.0) -> int:
    """The group's coordinator may still be electing on the first call of a
    session, which surfaces as NOT_COORDINATOR — a transient condition, not
    an offset of zero, so it is retried rather than read as an answer."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            partitions = await kafka_consumer.committed(
                [TopicPartition(REIMBURSEMENT_TOPIC, 0)], timeout=20.0
            )
        except KafkaException:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.5)
            continue
        offset = partitions[0].offset
        return offset if offset >= 0 else 0


async def _run_agent(
    config: Config,
    agent: AgentConfig,
    migrated_db: str,
    expected_advance: int,
    timeout: float = 90.0,
) -> None:
    """Drive the real loop until the offset has advanced by
    `expected_advance` (one per message, regardless of outcome), then stop
    it."""
    stopping = asyncio.Event()
    async with (
        managed_pool(replace(config.database, dsn=migrated_db)) as pool,
        managed_producer(config.kafka.to_producer_config()) as producer,
        managed_consumer(config, agent) as kafka_consumer,
    ):
        before = await _committed_offset(kafka_consumer)
        deps = Dependencies(config=config, agent=agent, pool=pool, producer=producer)
        task = asyncio.create_task(run(deps, kafka_consumer, stopping))
        deadline = time.monotonic() + timeout
        try:
            # A short per-tick timeout (not the default 30s): a stuck
            # coordinator would otherwise eat up to 30s inside one tick,
            # during which the outer deadline below never gets checked.
            advanced = await _committed_offset(kafka_consumer, timeout=2.0) - before
            while advanced < expected_advance:
                if time.monotonic() >= deadline:
                    raise AssertionError(f"offset advanced by only {advanced} of {expected_advance}")
                await asyncio.sleep(0.2)
                advanced = await _committed_offset(kafka_consumer, timeout=2.0) - before
        finally:
            stopping.set()
            await task


class DescribeTheEndToEndRoundTrip:
    async def it_resolves_a_fresh_message_for_a_real_row(
        self,
        kafka_bootstrap_server: str,
        migrated_db: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)

        # T14 wired agent.decide() into this same RESOLVED path -- stubbed
        # here so this test keeps proving only what it always proved (the
        # resolve branch is reached and logged, no DB side effect from the
        # resolve stage itself). The decision graph's own real, end-to-end
        # DB write is what DescribeTheDecisionGraph below proves.
        async def _stub_decide(
            reimbursement: object, pool: object, *, acquire_timeout_seconds: float
        ) -> dict[str, object]:
            return {"status": "auto-approved", "decision_reason": "stub", "persisted": True}

        monkeypatch.setattr(agent_module, "decide", _stub_decide)

        config, agent = _config()
        uuid = await _insert_row(migrated_db, "AGENT-E2E-RESOLVED")
        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        with caplog.at_level("INFO"):
            await _run_agent(config, agent, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "pending"  # unchanged — no side effect beyond the log
        # Distinguishes this branch from the ghost/stale ones below, which
        # leave the row equally untouched -- without this, a swapped branch
        # would pass all three tests.
        assert any(RESOLVED_EVENT in record.message for record in caplog.records)

    async def it_drops_a_message_whose_uuid_matches_no_row(
        self,
        kafka_bootstrap_server: str,
        migrated_db: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, agent = _config()
        ghost_uuid = uuid4()  # never inserted — R-001's dual-write window, simulated directly
        envelope = ReimbursementEnvelope(uuid=ghost_uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        # No exception, no hang — the offset still advances for a ghost.
        with caplog.at_level("INFO"):
            await _run_agent(config, agent, migrated_db, expected_advance=1)

        assert any(GHOST_DROPPED_EVENT in record.message for record in caplog.records)

    async def it_ignores_a_message_older_than_the_rows_last_update(
        self,
        kafka_bootstrap_server: str,
        migrated_db: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, agent = _config()
        uuid = await _insert_row(migrated_db, "AGENT-E2E-STALE")
        row_before = await _row(migrated_db, uuid)
        stale_published_at = row_before["updated_at"] - timedelta(hours=1)
        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=stale_published_at)
        await _produce(config, envelope)

        with caplog.at_level("INFO"):
            await _run_agent(config, agent, migrated_db, expected_advance=1)

        row_after = await _row(migrated_db, uuid)
        assert row_after["status"] == "pending"
        assert row_after["decision_reason"] is None
        assert any(STALE_IGNORED_EVENT in record.message for record in caplog.records)

    async def it_escalates_a_row_at_the_retry_ceiling_to_human_review(
        self, kafka_bootstrap_server: str, migrated_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, agent = _config()
        uuid = await _insert_row(migrated_db, "AGENT-E2E-ESCALATED")
        envelope = ReimbursementEnvelope(uuid=uuid, retry=4, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        await _run_agent(config, agent, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] is not None


class DescribeTheDecisionGraph:
    """T15: the real decision graph, exercised end to end against real
    Postgres and Kafka -- only the two Groq-bound models are faked (via
    build_graph's own dependency points), so no real network call to
    Groq ever happens. `apply_policies`/`apply_agent_decision` call the
    real `apply_decision` use case, which is what proves the row's
    `status`/`decision_reason` actually landed."""

    async def it_writes_an_auto_approved_status_and_reason_for_a_fresh_low_value_item(
        self,
        kafka_bootstrap_server: str,
        migrated_db: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, agent = _config()
        uuid = await _insert_row(migrated_db, "AGENT-E2E-DECIDED")

        # submitted_at is valid_reimbursement_item's fixed "2026-01-01
        # T12:00:00Z" -- same-day receipts_date is 0 days old, well inside
        # the 90-day reject window, and value=150 clears the <=200 ceiling:
        # apply_policies alone decides and persists, analysis never runs.
        extract_model = FakeStructuredModel(
            result=ExtractedFieldsSchema(value=150.0, currency="BRL", receipts_date=date(2026, 1, 1))
        )
        analysis_model = FakeStructuredModel(
            error=AssertionError("analysis/Groq must not be invoked for a <=200 item")
        )

        def _fake_build_graph() -> object:
            nodes = {
                "extract_fields": ExtractFields(
                    model=extract_model, model_name=DEFAULT_TEST_MODEL_NAME
                ),
                "validate": Validate(),
                "apply_policies": ApplyPolicies(apply_decision=apply_decision),
                "analysis": Analysis(model=analysis_model, model_name=DEFAULT_TEST_MODEL_NAME),
                "apply_agent_decision": ApplyAgentDecision(apply_decision=apply_decision),
            }
            return agent_module._wire(nodes)

        monkeypatch.setattr(agent_module, "build_graph", _fake_build_graph)

        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        with caplog.at_level("INFO"):
            await _run_agent(config, agent, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "auto-approved"
        assert row["decision_reason"] is not None
        # No real Groq call: the fake extraction model was invoked
        # exactly once (in-process, no network), and the analysis model
        # (which would raise if ever invoked) was never called at all.
        assert len(extract_model.calls) == 1
        assert len(analysis_model.calls) == 0
        assert any(DECIDED_EVENT in record.message for record in caplog.records)
        # AGD-23/24: this is the one test exercising the real, memoized
        # agent.decide() -> _langfuse_handlers() path end to end. langfuse
        # is a real dependency (FU-1) and CallbackHandler() construction
        # succeeds even without credentials configured (just a disabled
        # client), so the durable fallback must NOT have fired here — if it
        # had, tracing would be silently broken for every real decision.
        assert not any(
            agent_module.LANGFUSE_FALLBACK_EVENT in record.message for record in caplog.records
        )

    async def it_writes_a_human_review_status_via_the_apply_agent_decision_path(
        self,
        kafka_bootstrap_server: str,
        migrated_db: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Unlike the <=200 case above, this drives a value into the
        # ambiguous zone so the write lands via ApplyAgentDecision's own
        # call to the real apply_decision/repository.update_decision — a
        # call site the <=200 case's own ApplyPolicies write never
        # exercises against real Postgres.
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        config, agent = _config()
        uuid = await _insert_row(migrated_db, "AGENT-E2E-DECIDED-AMBIGUOUS")

        extract_model = FakeStructuredModel(
            result=ExtractedFieldsSchema(value=1000.0, currency="BRL", receipts_date=date(2026, 1, 1))
        )
        analysis_model = FakeStructuredModel(
            result=GuardrailVerdict(consistent=False, reason="claimed amount contradicts OCR total")
        )

        def _fake_build_graph() -> object:
            nodes = {
                "extract_fields": ExtractFields(
                    model=extract_model, model_name=DEFAULT_TEST_MODEL_NAME
                ),
                "validate": Validate(),
                "apply_policies": ApplyPolicies(apply_decision=apply_decision),
                "analysis": Analysis(model=analysis_model, model_name=DEFAULT_TEST_MODEL_NAME),
                "apply_agent_decision": ApplyAgentDecision(apply_decision=apply_decision),
            }
            return agent_module._wire(nodes)

        monkeypatch.setattr(agent_module, "build_graph", _fake_build_graph)

        envelope = ReimbursementEnvelope(uuid=uuid, retry=0, published_at=datetime.now(UTC))
        await _produce(config, envelope)

        with caplog.at_level("INFO"):
            await _run_agent(config, agent, migrated_db, expected_advance=1)

        row = await _row(migrated_db, uuid)
        assert row["status"] == "human-review"
        assert row["decision_reason"] == "claimed amount contradicts OCR total"
        assert len(extract_model.calls) == 1
        assert len(analysis_model.calls) == 1
        assert any(DECIDED_EVENT in record.message for record in caplog.records)
