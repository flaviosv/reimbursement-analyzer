from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from shared.tracing import (
    extract_context,
    init_tracer,
    inject_headers,
    shutdown_tracer,
    traced_message_span,
)


class _FakeMessage:
    def __init__(
        self,
        headers: list[tuple[str, bytes]] | None,
        topic: str = "Request",
        partition: int = 0,
        offset: int = 42,
    ) -> None:
        self._headers = headers
        self._topic = topic
        self._partition = partition
        self._offset = offset

    def headers(self) -> list[tuple[str, bytes]] | None:
        return self._headers

    def topic(self) -> str | None:
        return self._topic

    def partition(self) -> int | None:
        return self._partition

    def offset(self) -> int | None:
        return self._offset


def _in_memory_tracer() -> tuple[trace.Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer(__name__), exporter


class DescribeInitTracer:
    def it_registers_a_tracer_provider_with_the_given_service_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Spies on trace.set_tracer_provider rather than asserting on the
        # real global registry's current state: OTel's own set_tracer_
        # provider() silently refuses every call after the first in a
        # process, so asserting trace.get_tracer_provider() is provider
        # would pass or fail depending on whichever other test/module in
        # the same pytest run happened to register a provider first — this
        # spy proves init_tracer made the call, independent of that.
        registered: list[object] = []
        monkeypatch.setattr(trace, "set_tracer_provider", registered.append)

        provider = init_tracer("reimbursement-analyzer-api", "http://apm-server:8200")

        try:
            assert registered == [provider]
            assert provider.resource.attributes["service.name"] == "reimbursement-analyzer-api"
        finally:
            provider.shutdown()

    def it_points_the_exporter_at_the_v1_traces_suffix(self) -> None:
        provider = init_tracer("reimbursement-analyzer-api", "http://apm-server:8200/")

        try:
            processor = provider._active_span_processor._span_processors[0]
            assert processor.span_exporter._endpoint == "http://apm-server:8200/v1/traces"
        finally:
            provider.shutdown()


class DescribeShutdownTracer:
    def it_shuts_down_the_provider_without_raising(self) -> None:
        provider = init_tracer("reimbursement-analyzer-api", "http://apm-server:8200")

        shutdown_tracer(provider)

    def it_tolerates_being_called_on_an_already_shutdown_provider(self) -> None:
        provider = init_tracer("reimbursement-analyzer-api", "http://apm-server:8200")
        shutdown_tracer(provider)

        shutdown_tracer(provider)


class DescribeInjectHeaders:
    def it_serializes_the_current_span_context_as_byte_tuple_headers(self) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        tracer, _ = _in_memory_tracer()

        with tracer.start_as_current_span("parent"):
            headers = inject_headers()

        assert headers
        keys = {key for key, _ in headers}
        assert "traceparent" in keys
        for key, value in headers:
            assert isinstance(key, str)
            assert isinstance(value, bytes)

    def it_returns_an_empty_list_with_no_current_span(self) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())

        assert inject_headers() == []


class DescribeExtractContext:
    def it_extracts_a_parent_context_from_injected_headers(self) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        tracer, _ = _in_memory_tracer()

        with tracer.start_as_current_span("parent") as parent_span:
            headers = inject_headers()
            expected_trace_id = parent_span.get_span_context().trace_id

        context = extract_context(headers)
        extracted_span = trace.get_current_span(context)

        assert extracted_span.get_span_context().trace_id == expected_trace_id

    def it_returns_an_empty_context_for_none_headers(self) -> None:
        context = extract_context(None)

        assert trace.get_current_span(context) is trace.INVALID_SPAN

    def it_returns_an_empty_context_for_empty_headers(self) -> None:
        context = extract_context([])

        assert trace.get_current_span(context) is trace.INVALID_SPAN


class DescribeTracedMessageSpan:
    def it_starts_a_child_span_linked_to_the_message_headers_trace(self) -> None:
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        tracer, exporter = _in_memory_tracer()

        with tracer.start_as_current_span("producer-span") as producer_span:
            headers = inject_headers()
            expected_trace_id = producer_span.get_span_context().trace_id

        message = _FakeMessage(headers=headers)
        with traced_message_span(tracer, message):
            pass

        finished = exporter.get_finished_spans()
        process_span = next(s for s in finished if s.name == "process_message")
        assert process_span.context.trace_id == expected_trace_id

    def it_starts_a_new_root_span_when_the_message_carries_no_headers(self) -> None:
        tracer, exporter = _in_memory_tracer()

        message = _FakeMessage(headers=None)
        with traced_message_span(tracer, message):
            pass

        finished = exporter.get_finished_spans()
        process_span = next(s for s in finished if s.name == "process_message")
        assert process_span.parent is None

    def it_uses_process_message_as_the_default_span_name(self) -> None:
        tracer, exporter = _in_memory_tracer()

        with traced_message_span(tracer, _FakeMessage(headers=[])):
            pass

        assert exporter.get_finished_spans()[0].name == "process_message"

    def it_stamps_topic_partition_and_offset_as_span_attributes(self) -> None:
        tracer, exporter = _in_memory_tracer()
        message = _FakeMessage(headers=[], topic="Reimbursement", partition=3, offset=99)

        with traced_message_span(tracer, message):
            pass

        span = exporter.get_finished_spans()[0]
        assert span.attributes["messaging.kafka.topic"] == "Reimbursement"
        assert span.attributes["messaging.kafka.partition"] == 3
        assert span.attributes["messaging.kafka.offset"] == 99

    def it_accepts_a_custom_span_name(self) -> None:
        tracer, exporter = _in_memory_tracer()

        with traced_message_span(tracer, _FakeMessage(headers=[]), span_name="custom_span"):
            pass

        assert exporter.get_finished_spans()[0].name == "custom_span"
