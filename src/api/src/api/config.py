import os

# 25 MiB, inclusive: a body of exactly this many bytes is accepted; one byte
# more is rejected. Kept 1 MiB below KAFKA_MAX_MESSAGE_BYTES so the envelope
# prefix and protocol framing always fit inside what the broker will accept.
MAX_BODY_BYTES = 26_214_400

# 26 MiB. librdkafka's message.max.bytes default is 1_000_000 (range
# 1_000-1_000_000_000); this is raised on the broker, replica fetch, and
# producer sides so a MAX_BODY_BYTES-sized envelope is never rejected.
KAFKA_MAX_MESSAGE_BYTES = 27_262_976

# Below the AIOProducer-level publish timeout the create slice awaits, so
# librdkafka always fails first: a 500 means "definitely not delivered",
# never "not delivered yet".
MESSAGE_TIMEOUT_MS = 8000


def bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
