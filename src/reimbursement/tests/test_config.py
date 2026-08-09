import pytest
from config import load_agent_config
from shared.config import load_config


class DescribeAgentConfig:
    def it_defaults_the_consumer_group_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_CONSUMER_GROUP_ID", raising=False)

        config = load_agent_config()

        assert config.consumer_group_id == "agent"

    def it_reads_the_consumer_group_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_CONSUMER_GROUP_ID", "agent-canary")

        config = load_agent_config()

        assert config.consumer_group_id == "agent-canary"


class DescribeAgentConfigToConsumerConfig:
    def it_never_lets_librdkafka_commit_offsets_on_a_timer(self) -> None:
        config = load_agent_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["enable.auto.commit"] is False

    def it_subscribes_from_the_earliest_offset_under_the_configured_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_CONSUMER_GROUP_ID", "agent-canary")
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:9092")
        config = load_agent_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert consumer_config["group.id"] == "agent-canary"
        assert consumer_config["auto.offset.reset"] == "earliest"
        assert consumer_config["bootstrap.servers"] == "broker:9092"

    def it_reuses_the_kafka_security_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pass")
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/etc/ca.pem")
        config = load_agent_config()

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
        config = load_agent_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert not consumer_config.keys() & {
            "security.protocol",
            "sasl.mechanism",
            "sasl.username",
            "sasl.password",
            "ssl.ca.location",
        }

    def it_omits_the_fetch_size_overrides_the_publisher_needs(self) -> None:
        # Reimbursement messages are small, fixed-shape envelopes (uuid,
        # retry, published_at, errors) — unlike Request's large batch
        # payloads that motivate PublisherConfig's fetch-size sizing.
        config = load_agent_config()

        consumer_config = config.to_consumer_config(load_config().kafka)

        assert not consumer_config.keys() & {"fetch.max.bytes", "max.partition.fetch.bytes"}
        assert "max.poll.interval.ms" not in consumer_config
