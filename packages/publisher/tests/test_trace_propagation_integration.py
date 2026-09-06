"""Confirms `shared.tracing`'s inject/extract mechanism actually propagates
one shared trace ID through Kafka message headers, against a real broker,
across a simulated api -> publisher -> reimbursement chain (OTEL-04..08).

Exercises the exact functions each service's own consume loop calls
(`shared.producer.publish`, `shared.tracing.traced_message_span`) directly,
rather than running `api`/`publisher`/`reimbursement`'s own `_serve()`
entrypoints together in one process: each of those registers the process-
wide OTel `TracerProvider` once at import time, and OTel's own singleton
rule silently refuses every later `set_tracer_provider()` call — so a
locally-constructed `InMemorySpanExporter`-backed provider could never be
reached by whichever of those three modules' import happened to run first
in the shared pytest process. Calling the same reusable, provider-agnostic
helpers directly (each already unit-tested against fakes in the three
services' own test suites) proves the real propagation mechanism against a
real broker without fighting that singleton.
"""

import time

import pytest
from confluent_kafka import Consumer, Message
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shared.config import load_config
from shared.producer import managed_producer, publish
from shared.tracing import traced_message_span

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _consume_one(bootstrap_server: str, topic: str, timeout: float = 30.0) -> Message:
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_server,
            "group.id": f"trace-propagation-test-{time.monotonic_ns()}",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([topic])
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise AssertionError(f"consumer error: {msg.error()}")
            return msg
        raise AssertionError(f"no message on {topic!r} within {timeout}s")
    finally:
        consumer.close()


class DescribeTraceContextPropagationAcrossKafka:
    async def it_shares_one_trace_id_across_a_publish_consume_republish_consume_chain(
        self, kafka_bootstrap_server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        api_tracer = provider.get_tracer("api")
        publisher_tracer = provider.get_tracer("publisher")
        reimbursement_tracer = provider.get_tracer("reimbursement")

        request_topic = f"trace-request-{time.monotonic_ns()}"
        reimbursement_topic = f"trace-reimbursement-{time.monotonic_ns()}"
        kafka_config = load_config().kafka

        async with managed_producer(kafka_config.to_producer_config()) as producer:
            # Hop 1: api receives the HTTP request (simulated as a span) and
            # publishes a Request message — the header injection point.
            with api_tracer.start_as_current_span("POST /api/v1/reimbursement") as api_span:
                expected_trace_id = api_span.get_span_context().trace_id
                await publish(
                    producer, request_topic, b'{"hop": "request"}', kafka_config.publish_timeout_seconds
                )

            # Hop 2: publisher consumes the real Kafka message, extracts its
            # headers into a child span, and republishes a Reimbursement
            # message from inside that same span.
            request_message = _consume_one(kafka_bootstrap_server, request_topic)
            with traced_message_span(publisher_tracer, request_message) as publisher_span:
                assert publisher_span.get_span_context().trace_id == expected_trace_id
                assert publisher_span.attributes["messaging.kafka.topic"] == request_topic
                await publish(
                    producer,
                    reimbursement_topic,
                    b'{"hop": "reimbursement"}',
                    kafka_config.publish_timeout_seconds,
                )

        # Hop 3: reimbursement consumes that Reimbursement message the same
        # way — a child span of the same originating trace, not a new one.
        reimbursement_message = _consume_one(kafka_bootstrap_server, reimbursement_topic)
        with traced_message_span(reimbursement_tracer, reimbursement_message) as reimbursement_span:
            assert reimbursement_span.get_span_context().trace_id == expected_trace_id
            assert reimbursement_span.attributes["messaging.kafka.topic"] == reimbursement_topic

        # One connected trace across all three hops (P1 AC7) — not merely
        # pairwise-matching trace IDs asserted above, but every span this
        # chain produced landing under that exact one.
        finished = exporter.get_finished_spans()
        assert len(finished) == 3
        assert {span.context.trace_id for span in finished} == {expected_trace_id}

    async def it_starts_a_new_root_trace_when_the_message_carries_no_headers(
        self, kafka_bootstrap_server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # P1 AC8: a message produced by a path that predates header
        # injection (simulated here via the real sync confluent_kafka
        # Producer, bypassing shared.producer.publish's own injection)
        # still gets consumed and traced — as a new root, not a failure.
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", kafka_bootstrap_server)
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("publisher")
        topic = f"trace-no-headers-{time.monotonic_ns()}"
        kafka_config = load_config().kafka

        async with managed_producer(kafka_config.to_producer_config()) as producer:
            producer._producer.produce(topic=topic, value=b'{"hop": "headerless"}')
            producer._producer.flush(kafka_config.publish_timeout_seconds)

        message = _consume_one(kafka_bootstrap_server, topic)
        with traced_message_span(tracer, message) as span:
            pass

        finished = exporter.get_finished_spans()
        assert len(finished) == 1
        assert finished[0].parent is None
        assert span.get_span_context().trace_id == finished[0].context.trace_id
