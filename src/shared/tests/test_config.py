import pytest
from shared.config import (
    KAFKA_MAX_MESSAGE_BYTES,
    MAX_RETRY,
    REIMBURSEMENT_TOPIC,
    load_config,
)


class DescribeLoadConfig:
    def it_defaults_bootstrap_servers_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)

        config = load_config()

        assert config.kafka.bootstrap_servers == "localhost:9092"

    def it_reads_bootstrap_servers_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9092")

        config = load_config()

        assert config.kafka.bootstrap_servers == "broker:9092"

    def it_caches_across_calls_until_the_cache_is_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "first:9092")
        first = load_config()

        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "second:9092")
        second = load_config()

        assert first is second
        assert second.kafka.bootstrap_servers == "first:9092"

    def it_leaves_security_fields_unset_when_no_protocol_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)

        config = load_config()

        assert config.kafka.security_protocol is None
        producer_config = config.kafka.to_producer_config()
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

        config = load_config()

        assert config.kafka.to_producer_config() == {
            "bootstrap.servers": config.kafka.bootstrap_servers,
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


class DescribeWireConstants:
    def it_names_the_reimbursement_topic_and_the_retry_ceiling(self) -> None:
        # Both are cross-service wire values: the Agent subscribes to this
        # exact topic name, and SCOPE.md:215 sets the ceiling at "retry > 3".
        assert REIMBURSEMENT_TOPIC == "Reimbursement"
        assert MAX_RETRY == 3


class DescribeDatabaseConfig:
    def it_loads_with_no_database_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # api never touches Postgres, and load_config() is process-wide — an
        # os.environ[...] read here would break its boot.
        monkeypatch.delenv("DATABASE_URL", raising=False)

        config = load_config()

        assert config.database.dsn is None

    def it_reads_the_database_url_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db:5432/reimbursementanalyzer")

        config = load_config()

        assert config.database.dsn == "postgresql://user:pw@db:5432/reimbursementanalyzer"

    def it_sizes_the_pool_explicitly_rather_than_at_asyncpg_defaults(self) -> None:
        config = load_config()

        assert (config.database.pool_min_size, config.database.pool_max_size) != (10, 10)

    def it_sizes_the_pool_at_or_above_the_item_concurrency(self) -> None:
        # Each in-flight item holds a transaction open across a Kafka
        # round-trip, so concurrency N pins N connections (R-005).
        config = load_config()

        assert config.database.pool_max_size >= config.publisher.item_concurrency


class DescribeFailureLogConfig:
    def it_carries_a_logger_name_and_a_message_cap(self) -> None:
        config = load_config()

        assert config.failure_log.logger_name == "reimbursementanalyzer.failures"
        assert config.failure_log.max_message_chars > 0


class DescribePublisherConfig:
    def it_bounds_items_in_flight_at_ten(self) -> None:
        config = load_config()

        assert config.publisher.item_concurrency == 10

    def it_reads_the_consumer_group_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher-canary")

        config = load_config()

        assert config.publisher.consumer_group_id == "publisher-canary"


class DescribePublisherConfigToConsumerConfig:
    def it_never_lets_librdkafka_commit_offsets_on_a_timer(self) -> None:
        config = load_config()

        consumer_config = config.publisher.to_consumer_config(config.kafka)

        assert consumer_config["enable.auto.commit"] is False

    def it_sizes_both_fetch_limits_from_the_shared_message_ceiling(self) -> None:
        config = load_config()

        consumer_config = config.publisher.to_consumer_config(config.kafka)

        assert consumer_config["fetch.max.bytes"] == KAFKA_MAX_MESSAGE_BYTES
        assert consumer_config["max.partition.fetch.bytes"] == KAFKA_MAX_MESSAGE_BYTES

    def it_subscribes_from_the_earliest_offset_under_the_configured_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher-canary")
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9092")
        config = load_config()

        consumer_config = config.publisher.to_consumer_config(config.kafka)

        assert consumer_config["group.id"] == "publisher-canary"
        assert consumer_config["auto.offset.reset"] == "earliest"
        assert consumer_config["bootstrap.servers"] == "broker:9092"

    def it_reuses_the_kafka_security_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pass")
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/etc/ca.pem")
        config = load_config()

        consumer_config = config.publisher.to_consumer_config(config.kafka)

        assert consumer_config["security.protocol"] == "SASL_SSL"
        assert consumer_config["sasl.mechanism"] == "PLAIN"
        assert consumer_config["sasl.username"] == "user"
        assert consumer_config["sasl.password"] == "pass"
        assert consumer_config["ssl.ca.location"] == "/etc/ca.pem"

    def it_omits_the_security_settings_when_no_protocol_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)
        config = load_config()

        consumer_config = config.publisher.to_consumer_config(config.kafka)

        assert not consumer_config.keys() & {
            "security.protocol",
            "sasl.mechanism",
            "sasl.username",
            "sasl.password",
            "ssl.ca.location",
        }


class DescribeKafkaConfigToProducerConfig:
    def it_never_disables_retries_explicitly(self) -> None:
        # enable.idempotence=true rejects retries=0, and the envelope's own
        # `retry` counter is a separate, message-carried concept (not
        # librdkafka's producer-level retries property).
        config = load_config().kafka

        assert "retries" not in config.to_producer_config()

    def it_keeps_the_broker_timeout_below_the_asyncio_publish_timeout(self) -> None:
        # Below the AIOProducer-level publish timeout the caller awaits, so
        # librdkafka always fails first: a timeout-driven failure means
        # "definitely not delivered", never "not delivered yet".
        config = load_config().kafka

        assert config.message_timeout_ms / 1000 < config.publish_timeout_seconds
