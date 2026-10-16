"""Tests d'intégration : nécessitent Kafka et Redis lancés (`docker compose up -d`).

Ils sont ignorés, avec un message, si les services ne répondent pas.
"""

import asyncio
import uuid

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from redis.asyncio import Redis

from bus.topics import PARTITIONS, Topic, ensure_topics
from sim.settings import Settings, load_settings

pytestmark = pytest.mark.integration


async def _services_up(settings: Settings) -> bool:
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers, request_timeout_ms=3000
    )
    try:
        await redis.ping()
        await asyncio.wait_for(admin.start(), timeout=5)
        await admin.close()
        return True
    except Exception:
        return False
    finally:
        await redis.aclose()


@pytest.fixture(scope="module")
def settings() -> Settings:
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    return settings


def test_all_topics_exist_with_expected_settings(settings: Settings) -> None:
    async def scenario() -> None:
        await ensure_topics(settings.kafka_bootstrap_servers)
        admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
        await admin.start()
        try:
            names = [topic.value for topic in Topic]
            described = await admin.describe_topics(names)
            for topic in described:
                assert len(topic["partitions"]) == PARTITIONS, topic["topic"]
        finally:
            await admin.close()

    asyncio.run(scenario())


def test_kafka_roundtrip_preserves_order(settings: Settings) -> None:
    """Ce qui est écrit dans un topic est relu à l'identique et dans le même ordre."""
    topic = f"test.roundtrip.{uuid.uuid4().hex[:8]}"
    messages = [f"message {i}".encode() for i in range(20)]

    async def scenario() -> list[bytes]:
        admin = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers)
        await admin.start()
        await admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        try:
            await producer.start()
            for message in messages:
                await producer.send_and_wait(topic, message)
            await consumer.start()
            received: list[bytes] = []
            async with asyncio.timeout(15):
                while len(received) < len(messages):
                    batch = await consumer.getmany(timeout_ms=1000)
                    for records in batch.values():
                        received.extend(record.value for record in records)
            return received
        finally:
            await producer.stop()
            await consumer.stop()
            await admin.delete_topics([topic])
            await admin.close()

    assert asyncio.run(scenario()) == messages


def test_redis_roundtrip(settings: Settings) -> None:
    key = f"test:{uuid.uuid4().hex[:8]}"

    async def scenario() -> bytes | None:
        redis = Redis.from_url(settings.redis_url)
        try:
            await redis.hset(key, mapping={"zone": "plage", "porteurs": 3})
            value = await redis.hget(key, "zone")
            await redis.delete(key)
            return value
        finally:
            await redis.aclose()

    assert asyncio.run(scenario()) == b"plage"
