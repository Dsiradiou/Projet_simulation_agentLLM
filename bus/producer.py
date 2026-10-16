"""Publication d'événements sur Kafka. Chaque événement part sur le topic de son type."""

from types import TracebackType
from typing import Self

from aiokafka import AIOKafkaProducer

from bus.events import Envelope, encode, topic_for


class EventProducer:
    def __init__(self, bootstrap_servers: str) -> None:
        # acks="all" + idempotence : un événement accepté est écrit une fois, dans l'ordre d'envoi.
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers, acks="all", enable_idempotence=True
        )
        self.published = 0

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def publish(self, event: Envelope, topic: str | None = None) -> None:
        """Publie et attend l'accusé de réception. `topic` ne sert qu'aux tests."""
        await self._producer.send_and_wait(
            topic or topic_for(event).value, encode(event), key=event.run_id.encode("utf-8")
        )
        self.published += 1
