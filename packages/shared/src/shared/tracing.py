"""OpenTelemetry wiring shared by every service — TracerProvider init/
shutdown and the Kafka trace-context inject/extract helpers.

Every service calls into this module from its own composition root
(`api/main.py`'s module scope + `lifespan`; `publisher`/`reimbursement`'s
`_serve()`) rather than touching the OTel SDK directly, so the export
target, resource shape, and header wire format live in exactly one place.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Protocol

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer

KafkaHeaders = list[tuple[str, bytes]]


class _KafkaMessage(Protocol):
    """The subset of `confluent_kafka`'s consumed-message interface this
    module needs — both `AIOConsumer`'s message objects satisfy this
    without any adapter."""

    def headers(self) -> list[tuple[str, bytes]] | None: ...
    def topic(self) -> str | None: ...
    def partition(self) -> int | None: ...
    def offset(self) -> int | None: ...


def init_tracer(service_name: str, otlp_endpoint: str) -> TracerProvider:
    """Build a `TracerProvider` for `service_name`, exporting spans via
    OTLP/HTTP to `otlp_endpoint`, and register it as the process's global
    tracer provider. Sampling is left at the SDK's own default
    (`ParentBased(AlwaysOn)`) — no argument, no new configuration surface.

    `OTLPSpanExporter` only auto-appends `/v1/traces` when it resolves
    `OTEL_EXPORTER_OTLP_ENDPOINT` itself, not when `endpoint=` is passed
    explicitly — so it's appended here.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def shutdown_tracer(provider: TracerProvider) -> None:
    """Flush pending spans and shut the provider down cleanly. Safe to call
    once at process shutdown; `TracerProvider.shutdown()` itself tolerates
    being called on an already-shutdown provider."""
    provider.shutdown()


def inject_headers() -> KafkaHeaders:
    """Serialize the current trace context into the wire shape
    `confluent_kafka.Producer.produce(headers=...)` expects."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(key, value.encode()) for key, value in carrier.items()]


def extract_context(headers: KafkaHeaders | None) -> Context:
    """Deserialize Kafka message headers into an OTel `Context`. Absent or
    empty headers extract to an empty carrier, which OTel's own `extract()`
    turns into a context with no parent span — the caller's subsequent
    `start_as_current_span` then starts a new root trace, with no branching
    logic needed here."""
    carrier = {key: value.decode() for key, value in (headers or [])}
    return propagate.extract(carrier)


@contextmanager
def traced_message_span(
    tracer: Tracer, message: _KafkaMessage, span_name: str = "process_message"
) -> Generator[Span, None, None]:
    """Wrap one consumed message's handling in a span that is a child of
    whatever trace context its Kafka headers carried (or a new root, if
    none), carrying the message's topic/partition/offset as attributes.
    Used identically by `publisher`'s and `reimbursement`'s consume loops."""
    context = extract_context(message.headers())
    attributes: dict[str, Any] = {
        "messaging.kafka.topic": message.topic(),
        "messaging.kafka.partition": message.partition(),
        "messaging.kafka.offset": message.offset(),
    }
    with tracer.start_as_current_span(span_name, context=context, attributes=attributes) as span:
        yield span
