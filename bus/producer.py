"""Publication d'événements sur Kafka. Chaque événement part sur le topic de son type.

Chaque message porte deux en-têtes Kafka : `producteur` (qui publie) et `seq` (numéro de
publication chez ce producteur). Ils ne font pas partie de l'événement : ils donnent l'ordre
exact de publication entre topics, que l'historique conserve pour le rejeu (E3).
"""

from types import TracebackType
from typing import Self

from aiokafka import AIOKafkaProducer

from bus.events import Envelope, encode, topic_for


def ordre_publication(
    kafka_ts: int, producteur: str | None, seq: int | None
) -> tuple[int, str, int]:
    """Clé de tri qui redonne l'ordre de publication entre topics : horodatage Kafka (ms), puis
    producteur et numéro de publication, qui départagent les messages d'une même milliseconde.
    Partagée par la console en direct et le rejeu, pour un affichage identique."""
    return (kafka_ts, producteur or "", seq or 0)


def ordre_record(record: object) -> tuple[int, str, int]:
    """`ordre_publication` d'un message Kafka reçu (aiokafka `ConsumerRecord`)."""
    headers = {k: v.decode("utf-8") for k, v in (getattr(record, "headers", None) or ())}
    seq = headers.get("seq")
    return ordre_publication(record.timestamp, headers.get("producteur"), int(seq) if seq else None)


class EventProducer:
    def __init__(self, bootstrap_servers: str, producteur: str = "orchestrateur") -> None:
        # acks="all" + idempotence : un événement accepté est écrit une fois, dans l'ordre d'envoi.
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers, acks="all", enable_idempotence=True
        )
        self.producteur = producteur
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
        headers = [
            ("producteur", self.producteur.encode("utf-8")),
            ("seq", str(self.published).encode("ascii")),
        ]
        await self._producer.send_and_wait(
            topic or topic_for(event).value,
            encode(event),
            key=event.run_id.encode("utf-8"),
            headers=headers,
        )
        self.published += 1
