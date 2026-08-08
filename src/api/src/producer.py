from confluent_kafka.aio import AIOProducer
from fastapi import Request
from shared.config import KafkaConfig, load_config


def get_producer(request: Request) -> AIOProducer:
    return request.app.state.producer


def get_kafka_config() -> KafkaConfig:
    return load_config().kafka
