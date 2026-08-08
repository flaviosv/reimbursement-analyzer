from pathlib import Path

import yaml
from shared.config import KAFKA_MAX_MESSAGE_BYTES, MAX_BODY_BYTES

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"

# Read and parsed once at import time rather than per-test: the file never
# changes mid-session, and re-reading/re-parsing 12.8 KB of YAML on every
# assertion is uncached I/O that only grows with future tests against it.
_KAFKA_ENVIRONMENT = yaml.safe_load(COMPOSE_PATH.read_text())["services"]["kafka"]["environment"]


class DescribeComposeParity:
    def it_ships_a_broker_message_max_bytes_matching_the_config_constant(self) -> None:
        assert _KAFKA_ENVIRONMENT["KAFKA_MESSAGE_MAX_BYTES"] == KAFKA_MAX_MESSAGE_BYTES

    def it_ships_a_replica_fetch_max_bytes_matching_the_config_constant(self) -> None:
        assert _KAFKA_ENVIRONMENT["KAFKA_REPLICA_FETCH_MAX_BYTES"] == KAFKA_MAX_MESSAGE_BYTES

    def it_gives_the_kafka_ceiling_headroom_over_the_http_body_ceiling(self) -> None:
        # 1 MiB of headroom for the envelope prefix and protocol framing.
        assert KAFKA_MAX_MESSAGE_BYTES > MAX_BODY_BYTES
