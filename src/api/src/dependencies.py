from confluent_kafka.aio import AIOProducer
from fastapi import Request


def get_producer(request: Request) -> AIOProducer:
    return request.app.state.producer
