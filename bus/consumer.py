"""Lecture d'événements sur Kafka, avec validation et isolement des messages non conformes (F4).

Un message qui ne respecte aucun schéma est publié dans `sim.dlq`, compté, puis ignoré : la
simulation continue.
"""

from collections.abc import Callable, Iterable

from aiokafka import AIOKafkaConsumer

from bus.events import (
    DLQ_RAW_MAX,
    Event,
    EventValidationError,
    Rejection,
    RejectionPayload,
    decode,
)
from bus.producer import EventProducer
from bus.topics import Topic


class EventConsumer:
    def __init__(
        self,
        bootstrap_servers: str,
        topics: Iterable[str],
        *,
        dlq: EventProducer,
        run_id: str,
        sim_now: Callable[[], int] = lambda: 0,
        group_id: str | None = None,
        dlq_topic: str = Topic.DLQ.value,
    ) -> None:
        """`run_id` et `sim_now` datent les rejets : un message illisible n'a pas de date fiable."""
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=group_id is not None,
        )
        self._dlq = dlq
        self._dlq_topic = dlq_topic
        self._run_id = run_id
        self._sim_now = sim_now
        self.received = 0
        self.rejected = 0

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def poll(self, timeout_ms: int = 1000) -> list[Event]:
        """Renvoie les événements valides disponibles, dans l'ordre de chaque topic."""
        events: list[Event] = []
        batches = await self._consumer.getmany(timeout_ms=timeout_ms)
        for partition, records in batches.items():
            for record in records:
                self.received += 1
                try:
                    events.append(decode(record.value))
                except EventValidationError as exc:
                    await self._reject(partition.topic, record.value, str(exc))
        return events

    async def _reject(self, topic: str, raw: bytes, error: str) -> None:
        self.rejected += 1
        now = self._sim_now()
        rejection = Rejection(
            run_id=self._run_id,
            sim_time_occurred=now,
            sim_time_processed=now,
            source_agent_id="bus",
            payload=RejectionPayload(
                topic_origine=topic,
                brut=raw.decode("utf-8", errors="replace")[:DLQ_RAW_MAX],
                erreur=error,
            ),
        )
        await self._dlq.publish(rejection, topic=self._dlq_topic)
