import dataclasses
from pathlib import Path

import pytest
from reimbursement.config import AIConfig, ModelConfig, load_agent_config
from shared.config import load_config

_ENV_SAMPLE_PATH = Path(__file__).resolve().parents[3] / ".env.sample"
_REQUIRED_AI_ENV_KEYS = {
    "GROQ_API_KEY",
    "AI_TIMEOUT_SECONDS",
    "EXTRACT_FIELDS_MODEL_NAME",
    "EXTRACT_FIELDS_TEMPERATURE",
    "ANALYSIS_MODEL_NAME",
    "ANALYSIS_TEMPERATURE",
}


@pytest.fixture(autouse=True)
def _default_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # T2 (conftest.py) supplies this same default globally for every other
    # test in the package; this file's own tests exercise the fail-fast/
    # default behavior directly, so each test overrides/deletes the one var
    # it cares about on top of this baseline.
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_placeholder")
    monkeypatch.setenv("EXTRACT_FIELDS_MODEL_NAME", "llama-3.3-70b-versatile")
    monkeypatch.setenv("ANALYSIS_MODEL_NAME", "llama-3.3-70b-versatile")


class DescribeAgentConfig:
    def it_defaults_the_consumer_group_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENT_CONSUMER_GROUP_ID", raising=False)

        config = load_agent_config()

        assert config.consumer_group_id == "agent"

    def it_reads_the_consumer_group_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_CONSUMER_GROUP_ID", "agent-canary")

        config = load_agent_config()

        assert config.consumer_group_id == "agent-canary"

    def it_raises_when_groq_api_key_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            load_agent_config()

    def it_raises_when_groq_api_key_is_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "")

        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            load_agent_config()

    def it_raises_when_extract_fields_model_name_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTRACT_FIELDS_MODEL_NAME", raising=False)

        with pytest.raises(ValueError, match="EXTRACT_FIELDS_MODEL_NAME"):
            load_agent_config()

    def it_raises_when_extract_fields_model_name_is_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTRACT_FIELDS_MODEL_NAME", "")

        with pytest.raises(ValueError, match="EXTRACT_FIELDS_MODEL_NAME"):
            load_agent_config()

    def it_raises_when_analysis_model_name_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANALYSIS_MODEL_NAME", raising=False)

        with pytest.raises(ValueError, match="ANALYSIS_MODEL_NAME"):
            load_agent_config()

    def it_raises_when_analysis_model_name_is_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANALYSIS_MODEL_NAME", "")

        with pytest.raises(ValueError, match="ANALYSIS_MODEL_NAME"):
            load_agent_config()

    def it_raises_naming_the_variable_when_ai_timeout_seconds_is_not_numeric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_TIMEOUT_SECONDS", "abc")

        with pytest.raises(ValueError, match="AI_TIMEOUT_SECONDS"):
            load_agent_config()

    def it_raises_naming_the_variable_when_extract_fields_temperature_is_not_numeric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXTRACT_FIELDS_TEMPERATURE", "abc")

        with pytest.raises(ValueError, match="EXTRACT_FIELDS_TEMPERATURE"):
            load_agent_config()

    def it_raises_naming_the_variable_when_analysis_temperature_is_not_numeric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANALYSIS_TEMPERATURE", "abc")

        with pytest.raises(ValueError, match="ANALYSIS_TEMPERATURE"):
            load_agent_config()

    def it_defaults_ai_timeout_seconds_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_TIMEOUT_SECONDS", raising=False)

        config = load_agent_config()

        assert config.ai.timeout_seconds == 30.0

    def it_defaults_extract_fields_temperature_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTRACT_FIELDS_TEMPERATURE", raising=False)

        config = load_agent_config()

        assert config.models.extract_fields.temperature == 0.0

    def it_defaults_analysis_temperature_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANALYSIS_TEMPERATURE", raising=False)

        config = load_agent_config()

        assert config.models.analysis.temperature == 0.0

    def it_reads_the_ai_and_model_settings_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_real")
        monkeypatch.setenv("AI_TIMEOUT_SECONDS", "45")
        monkeypatch.setenv("EXTRACT_FIELDS_MODEL_NAME", "model-a")
        monkeypatch.setenv("EXTRACT_FIELDS_TEMPERATURE", "0.2")
        monkeypatch.setenv("ANALYSIS_MODEL_NAME", "model-b")
        monkeypatch.setenv("ANALYSIS_TEMPERATURE", "0.5")

        config = load_agent_config()

        assert config.ai.api_key == "gsk_real"
        assert config.ai.timeout_seconds == 45.0
        assert config.models.extract_fields.model_name == "model-a"
        assert config.models.extract_fields.temperature == 0.2
        assert config.models.analysis.model_name == "model-b"
        assert config.models.analysis.temperature == 0.5

    def it_keeps_api_key_only_on_the_shared_ai_config_not_per_node(self) -> None:
        # AMC-13/14: api_key has exactly one home (AIConfig, shared by both
        # nodes) — ModelConfig structurally cannot carry a second, drifting
        # copy of the credential.
        model_config_fields = {f.name for f in dataclasses.fields(ModelConfig)}
        ai_config_fields = {f.name for f in dataclasses.fields(AIConfig)}

        assert "api_key" not in model_config_fields
        assert "api_key" in ai_config_fields


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


class DescribeEnvSampleParity:
    def it_carries_all_six_ai_env_vars_with_non_blank_placeholders(self) -> None:
        lines = _ENV_SAMPLE_PATH.read_text().splitlines()
        values = dict(
            line.split("=", 1) for line in lines if "=" in line and not line.startswith("#")
        )

        assert _REQUIRED_AI_ENV_KEYS <= values.keys()
        for key in _REQUIRED_AI_ENV_KEYS:
            assert values[key].strip() != "", f"{key} must carry a non-blank placeholder"
