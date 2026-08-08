import pytest
from shared.config import kafka_security_config


class DescribeKafkaSecurityConfig:
    def it_returns_empty_when_protocol_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)

        assert kafka_security_config() == {}

    def it_includes_only_the_variables_that_are_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.delenv("KAFKA_SASL_USERNAME", raising=False)
        monkeypatch.delenv("KAFKA_SASL_PASSWORD", raising=False)
        monkeypatch.delenv("KAFKA_SSL_CA_LOCATION", raising=False)

        config = kafka_security_config()

        assert config == {"security.protocol": "SASL_SSL", "sasl.mechanism": "PLAIN"}

    def it_includes_every_variable_when_all_are_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
        monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
        monkeypatch.setenv("KAFKA_SASL_USERNAME", "user")
        monkeypatch.setenv("KAFKA_SASL_PASSWORD", "pass")
        monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/etc/ca.pem")

        config = kafka_security_config()

        assert config == {
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": "PLAIN",
            "sasl.username": "user",
            "sasl.password": "pass",
            "ssl.ca.location": "/etc/ca.pem",
        }
