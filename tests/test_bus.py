"""Tests d'intégration du producteur et du consommateur : nécessitent Kafka (docker compose)."""

import asyncio
import json
import uuid

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from bus import events as ev
from bus.consumer import EventConsumer
from bus.producer import EventProducer
from sim.settings import load_settings
from tests.exemples_evenements import RUN, exemples
from tests.test_infra import _services_up

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def bootstrap() -> str:
    settings = load_settings()
    if not asyncio.run(_services_up(settings)):
        pytest.skip("Kafka ou Redis injoignable : lancer `docker compose up -d`")
    return settings.kafka_bootstrap_servers


def test_invalid_messages_go_to_dlq_without_stopping(bootstrap: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    source, dlq = f"test.source.{suffix}", f"test.dlq.{suffix}"
    valid = [exemples()[ev.AgentDecision], exemples()[ev.Weather]]
    wrong_version = json.loads(ev.encode(exemples()[ev.Command]))
    wrong_version["schema_version"] = "9.9"
    sequence = [
        ev.encode(valid[0]),
        b"pas du json",
        json.dumps(wrong_version).encode(),
        ev.encode(valid[1]),
    ]

    async def scenario() -> tuple[list[ev.Envelope], int, int, list[ev.Envelope]]:
        admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
        await admin.start()
        await admin.create_topics(
            [NewTopic(name, num_partitions=1, replication_factor=1) for name in (source, dlq)]
        )
        raw_producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
        producer = EventProducer(bootstrap)
        consumer = EventConsumer(bootstrap, [source], dlq=producer, run_id=RUN, dlq_topic=dlq)
        dlq_reader = AIOKafkaConsumer(
            dlq, bootstrap_servers=bootstrap, auto_offset_reset="earliest"
        )
        try:
            await raw_producer.start()
            await producer.start()
            for raw in sequence:
                await raw_producer.send_and_wait(source, raw)
            await consumer.start()
            received: list[ev.Envelope] = []
            async with asyncio.timeout(15):
                while consumer.received < len(sequence):
                    received += await consumer.poll(timeout_ms=500)
            await dlq_reader.start()
            rejected: list[ev.Envelope] = []
            async with asyncio.timeout(15):
                while len(rejected) < 2:
                    batch = await dlq_reader.getmany(timeout_ms=500)
                    rejected += [ev.decode(r.value) for rs in batch.values() for r in rs]
            return received, consumer.received, consumer.rejected, rejected
        finally:
            for client in (raw_producer, producer, consumer, dlq_reader):
                await client.stop()
            await admin.delete_topics([source, dlq])
            await admin.close()

    received, total, rejected_count, rejections = asyncio.run(scenario())

    assert received == valid  # les messages valides passent, dans l'ordre, malgré les rejets
    assert (total, rejected_count) == (4, 2)
    assert all(isinstance(r, ev.Rejection) for r in rejections)
    assert {r.payload.topic_origine for r in rejections} == {source}
    assert "JSON illisible" in rejections[0].payload.erreur
    assert "schema_version" in rejections[1].payload.erreur
