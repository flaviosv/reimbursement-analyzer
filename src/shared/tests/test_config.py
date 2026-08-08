import pytest
from shared.config import KAFKA_MAX_MESSAGE_BYTES, KafkaConfig


class DescribeKafkaConfigFromEnv:
    def it_defaults_bootstrap_servers_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)

        config = KafkaConfig.from_env()

        assert config.bootstrap_servers == "localhost:9092"

    def it_reads_bootstrap_servers_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9092")

        config = KafkaConfig.from_env()

        assert config.bootstrap_servers == "broker:9092"

    def it_leaves_security_fields_unset_when_no_protocol_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)

        config = KafkaConfig.from_env()

        assert config.security_protocol is None
        producer_config = config.to_producer_config()
        assert not producer_config.keys() & {
            "security.protocol",
            "sasl.mechanism",
            "sasl.username",
            "sasl.password",
            "ssl.ca.location",
        }

    def it_reads_every_security_variable_when_all_are_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pass")
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/etc/ca.pem")

        config = KafkaConfig.from_env()

        assert config.to_producer_config() == {
            "bootstrap.servers": config.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "message.max.bytes": KAFKA_MAX_MESSAGE_BYTES,
            "message.timeout.ms": 8000,
            "queue.buffering.max.kbytes": 200 * (KAFKA_MAX_MESSAGE_BYTES // 1024),
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": "PLAIN",
            "sasl.username": "user",
            "sasl.password": "pass",
            "ssl.ca.location": "/etc/ca.pem",
        }


class DescribeKafkaConfigToProducerConfig:
    def it_never_disables_retries_explicitly(self) -> None:
        # enable.idempotence=true rejects retries=0, and the envelope's own
        # `retry` counter is a separate, message-carried concept (not
        # librdkafka's producer-level retries property).
        config = KafkaConfig()

        assert "retries" not in config.to_producer_config()

    def it_keeps_the_broker_timeout_below_the_asyncio_publish_timeout(self) -> None:
        # Below the AIOProducer-level publish timeout the caller awaits, so
        # librdkafka always fails first: a timeout-driven failure means
        # "definitely not delivered", never "not delivered yet".
        config = KafkaConfig()

        assert config.message_timeout_ms / 1000 < config.publish_timeout_seconds
