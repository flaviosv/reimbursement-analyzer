import os

from confluent_kafka import Consumer
from shared.models import SampleMessage

# Placeholder queue name for structural validation ahead of the real infra setup.
QUEUE = "sample-queue"


def main() -> None:
    consumer = Consumer(
        {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": "publisher-sample-consumer",
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([QUEUE])

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"Consumer error: {msg.error()}")
                continue

            message = SampleMessage.model_validate_json(msg.value())
            print(f"Received message: {message}")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
