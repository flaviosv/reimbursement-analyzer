from pathlib import Path

import yaml
from api.config import KAFKA_MAX_MESSAGE_BYTES, MAX_BODY_BYTES

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"


def _kafka_environment() -> dict:
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    return compose["services"]["kafka"]["environment"]


class DescribeComposeParity:
    def it_ships_a_broker_message_max_bytes_matching_the_config_constant(self) -> None:
        assert _kafka_environment()["KAFKA_MESSAGE_MAX_BYTES"] == KAFKA_MAX_MESSAGE_BYTES

    def it_ships_a_replica_fetch_max_bytes_matching_the_config_constant(self) -> None:
        assert _kafka_environment()["KAFKA_REPLICA_FETCH_MAX_BYTES"] == KAFKA_MAX_MESSAGE_BYTES

    def it_gives_the_kafka_ceiling_headroom_over_the_http_body_ceiling(self) -> None:
        # 1 MiB of headroom for the envelope prefix and protocol framing.
        assert KAFKA_MAX_MESSAGE_BYTES > MAX_BODY_BYTES
