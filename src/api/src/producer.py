from confluent_kafka.aio import AIOProducer
from fastapi import Request
from shared.config import KafkaConfig


def get_producer(request: Request) -> AIOProducer:
    return request.app.state.producer


def get_kafka_config(request: Request) -> KafkaConfig:
    return request.app.state.kafka_config
