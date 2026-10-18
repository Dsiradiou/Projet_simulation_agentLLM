"""Commandes et injections venues de l'interface (D3), lues par l'orchestrateur sur `sim.control`.

Le pont publie sur Kafka ; l'orchestrateur relève ce qui est arrivé au début de chaque jour
simulé, jamais au milieu (barrière synchrone, C6). Un message non conforme part dans `sim.dlq`.
"""

from typing import Protocol

from bus.consumer import EventConsumer
from bus.events import Command, Injection
from bus.producer import EventProducer
from bus.topics import Topic


class SourceControle(Protocol):
    async def relever(self) -> list[Injection | Command]: ...


class LecteurControle:
    """Lit `sim.control` à partir du lancement du run et ne garde que les messages de ce run."""

    def __init__(self, bootstrap_servers: str, run_id: str, dlq: EventProducer) -> None:
        self.run_id = run_id
        self._consumer = EventConsumer(
            bootstrap_servers,
            [Topic.CONTROL.value],
            dlq=dlq,
            run_id=run_id,
            depuis_fin=True,
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def relever(self) -> list[Injection | Command]:
        events = await self._consumer.poll(timeout_ms=50)
        return [e for e in events if isinstance(e, Injection | Command) and e.run_id == self.run_id]
