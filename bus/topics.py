"""Les 7 topics Kafka de la simulation et leur création.

Usage : `python -m bus.topics` crée les topics manquants (la création automatique est désactivée
côté broker, voir docker-compose.yml).
"""

import asyncio
from enum import StrEnum

from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from sim.settings import load_settings


class Topic(StrEnum):
    WORLD_EVENTS = "sim.world.events"  # météo, stocks, promotions, événements injectés
    AGENT_MESSAGES = "sim.agent.messages"  # échanges en langage naturel entre agents (A2)
    AGENT_DECISIONS = "sim.agent.decisions"  # action et sens déclarés par chaque agent
    INDICATORS = "sim.indicators"  # indicateurs calculés pendant le run (B2)
    CONTROL = "sim.control"  # commandes et injections depuis l'interface (D3)
    INCIDENTS = "sim.incidents"  # sorties LLM invalides, replis, violations temporelles (A4, C6)
    DLQ = "sim.dlq"  # événements non conformes à leur schéma (F4)


# Une seule partition par topic : Kafka garantit alors un ordre total des événements du topic,
# condition d'un rejeu identique (E3). Le débit d'une partition dépasse largement nos besoins.
PARTITIONS = 1
REPLICATION_FACTOR = 1  # un seul broker
RETENTION_MS = 7 * 24 * 3600 * 1000  # rétention courte : l'historique durable est en Parquet


async def ensure_topics(bootstrap_servers: str) -> list[str]:
    """Crée les topics absents et renvoie la liste de ceux qui ont été créés."""
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [topic for topic in Topic if topic.value not in existing]
        if missing:
            await admin.create_topics(
                [
                    NewTopic(
                        name=topic.value,
                        num_partitions=PARTITIONS,
                        replication_factor=REPLICATION_FACTOR,
                        topic_configs={"retention.ms": str(RETENTION_MS)},
                    )
                    for topic in missing
                ]
            )
        return [topic.value for topic in missing]
    finally:
        await admin.close()


def main() -> None:
    settings = load_settings()
    created = asyncio.run(ensure_topics(settings.kafka_bootstrap_servers))
    for topic in Topic:
        status = "créé" if topic.value in created else "déjà présent"
        print(f"{topic.value:<22} {status}")


if __name__ == "__main__":
    main()
