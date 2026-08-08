import pytest
from shared.kafka import managed_producer, producer_config

pytestmark = pytest.mark.anyio


class DescribeProducerConfig:
    def it_sets_kafka_config_from_the_given_values(self) -> None:
        config = producer_config(
            bootstrap_servers="localhost:9092",
            message_max_bytes=2_097_152,
            message_timeout_ms=8000,
        )

        assert config == {
            "bootstrap.servers": "localhost:9092",
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": 2_097_152,
            "message.timeout.ms": 8000,
        }

    def it_leaves_retries_at_the_librdkafka_default(self) -> None:
        # enable.idempotence=true rejects retries=0, and the envelope's own
        # `retry` counter is a separate, message-carried concept (not
        # librdkafka's producer-level retries property).
        config = producer_config("localhost:9092", 2_097_152, 8000)

        assert "retries" not in config

    def it_adds_queue_buffering_max_kbytes_only_when_given(self) -> None:
        without = producer_config("localhost:9092", 1, 1)
        with_value = producer_config("localhost:9092", 1, 1, queue_buffering_max_kbytes=100)

        assert "queue.buffering.max.kbytes" not in without
        assert with_value["queue.buffering.max.kbytes"] == 100

    def it_merges_security_settings_when_given(self) -> None:
        config = producer_config(
            "localhost:9092", 1, 1, security={"security.protocol": "SASL_SSL"}
        )

        assert config["security.protocol"] == "SASL_SSL"


class DescribeManagedProducer:
    async def it_closes_the_producer_on_clean_exit(self) -> None:
        async with managed_producer({"bootstrap.servers": "localhost:9092"}) as producer:
            pass

        assert producer._is_closed is True

    async def it_closes_the_producer_even_when_the_body_raises(self) -> None:
        captured = None
        with pytest.raises(RuntimeError):
            async with managed_producer({"bootstrap.servers": "localhost:9092"}) as producer:
                captured = producer
                raise RuntimeError("boom")

        assert captured is not None
        assert captured._is_closed is True
