import os

# --- topics ---------------------------------------------------------------
# Shared across every producer/consumer that touches this topic — the
# envelope byte-shape in shared.models.RequestEnvelope is this contract's
# other half.
REQUEST_TOPIC = "Request"

# --- batch cardinality (AD-013) ---------------------------------------------
# 500 items: at MAX_BODY_BYTES (1 MiB) this leaves ~2,097 bytes/item of
# headroom — ~5.5x the average item size in docs/original/sample.json —
# while independently bounding per-item validation cost (EmailStr regex,
# AwareDatetime parsing) that the byte-size cap doesn't limit. A cross-service
# wire constant, not api-local: the publisher's own item-concurrency timing
# (AD-013) is computed against this same number.
MAX_BATCH_ITEMS = 500

# --- http body ceiling ------------------------------------------------------
# 1 MiB, inclusive: a body of exactly this many bytes is accepted; one byte
# more is rejected. Sized against docs/original/sample.json: the sample's
# average item is ~381 bytes of compact JSON; the 500-item batch cap
# (reimbursement.create.validation.BATCH_ADAPTER) leaves ~2,097 bytes/item of
# headroom at this ceiling — ~5.5x the sample's average item size.
MAX_BODY_BYTES = 1_048_576

# --- kafka tuning -----------------------------------------------------------
# 1 MiB above MAX_BODY_BYTES so the envelope prefix and protocol framing
# always fit inside what the broker will accept. librdkafka's
# message.max.bytes default is 1_000_000 (range 1_000-1_000_000_000); this is
# raised on the broker, replica fetch, and producer sides so a
# MAX_BODY_BYTES-sized envelope is never rejected.
KAFKA_MAX_MESSAGE_BYTES = 2_097_152

# Below the AIOProducer-level publish timeout the caller awaits, so
# librdkafka always fails first: a timeout-driven failure means "definitely
# not delivered", never "not delivered yet".
MESSAGE_TIMEOUT_MS = 8000


def bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


# --- kafka security (SASL/TLS) ----------------------------------------------
# Unset by default (PLAINTEXT, librdkafka's own default) so local/dev compose
# keeps working without extra setup. Set KAFKA_SECURITY_PROTOCOL (plus the
# SASL/TLS variables it requires) to switch a deployment to authenticated,
# encrypted transport.
def kafka_security_config() -> dict[str, str]:
    protocol = os.environ.get("KAFKA_SECURITY_PROTOCOL")
    if not protocol:
        return {}
    config = {"security.protocol": protocol}
    mechanism = os.environ.get("KAFKA_SASL_MECHANISM")
    username = os.environ.get("KAFKA_SASL_USERNAME")
    password = os.environ.get("KAFKA_SASL_PASSWORD")
    ca_location = os.environ.get("KAFKA_SSL_CA_LOCATION")
    if mechanism:
        config["sasl.mechanism"] = mechanism
    if username:
        config["sasl.username"] = username
    if password:
        config["sasl.password"] = password
    if ca_location:
        config["ssl.ca.location"] = ca_location
    return config
