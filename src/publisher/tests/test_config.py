import pytest
from config import load_publisher_config
from shared.config import KAFKA_MAX_MESSAGE_BYTES, load_config


class DescribePublisherConfig:
    def it_bounds_items_in_flight_at_ten(self) -> None:
        config = load_publisher_config()

        assert config.item_concurrency == 10

    def it_reads_item_concurrency_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Configurable, not just a literal — see the matching
        # DescribeDatabaseConfig test in shared: making both sides of the
        # comparison configurable is what makes consumer.check_startup_config
        # able to actually fail (A7/Q4/H8/P11 — it previously could not, since
        # both were hardcoded to values that always satisfied the guard).
        monkeypatch.setenv("PUBLISHER_ITEM_CONCURRENCY", "25")

        config = load_publisher_config()

        assert config.item_concurrency == 25

    def it_reads_the_consumer_group_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher-canary")

        config = load_publisher_config()

        assert config.consumer_group_id == "publisher-canary"


class DescribePublisherConfigToConsumerConfig:
    def it_never_lets_librdkafka_commit_offsets_on_a_timer(self) -> None:
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["enable.auto.commit"] is False

    def it_states_the_poll_interval_budget_rather_than_inheriting_it(self) -> None:
        # AD-013's `500 ÷ 10 × ~15ms` headroom is sized against this interval.
        # Left at librdkafka's default it is a budget no file in the repo names.
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert config.max_poll_interval_ms == 900_000
        assert consumer_config["max.poll.interval.ms"] == 900_000

    def it_sizes_both_fetch_limits_from_the_shared_message_ceiling(self) -> None:
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["fetch.max.bytes"] == KAFKA_MAX_MESSAGE_BYTES
        assert consumer_config["max.partition.fetch.bytes"] == KAFKA_MAX_MESSAGE_BYTES

    def it_subscribes_from_the_earliest_offset_under_the_configured_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PUBLISHER_CONSUMER_GROUP_ID", "publisher-canary")
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9092")
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["group.id"] == "publisher-canary"
        assert consumer_config["auto.offset.reset"] == "earliest"
        assert consumer_config["bootstrap.servers"] == "broker:9092"

    def it_reuses_the_kafka_security_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pass")
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/etc/ca.pem")
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["security.protocol"] == "SASL_SSL"
        assert consumer_config["sasl.mechanism"] == "PLAIN"
        assert consumer_config["sasl.username"] == "user"
        assert consumer_config["sasl.password"] == "pass"
        assert consumer_config["ssl.ca.location"] == "/etc/ca.pem"

    def it_omits_the_security_settings_when_no_protocol_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)
        config = load_publisher_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert not consumer_config.keys() & {
            "security.protocol",
            "sasl.mechanism",
            "sasl.username",
            "sasl.password",
            "ssl.ca.location",
        }
