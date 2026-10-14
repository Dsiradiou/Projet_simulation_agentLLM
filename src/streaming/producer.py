"""Producteur Kafka pour la simulation Mondes Vivants.
Publie les événements sur les topics Kafka sous format JSON validé.
"""
import json
import logging
from typing import Optional
from kafka import KafkaProducer
from kafka.errors import KafkaError

from src.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_EVENTS
from src.schemas.events import BaseSimulationEvent

logger = logging.getLogger(__name__)


class SimulationEventProducer:
    def __init__(self, bootstrap_servers: Optional[str] = None):
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP_SERVERS
        self.producer = KafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            retries=5,
            acks="all"
        )
        logger.info(f"SimulationEventProducer connecté à {self.bootstrap_servers}")

    def emit_event(self, event: BaseSimulationEvent, topic: str = TOPIC_EVENTS):
        """Publie un événement typé et validé par Pydantic sur Kafka."""
        try:
            # Sérialisation vers un dictionnaire JSON propre
            payload_dict = event.model_dump(mode="json")
            # Clé de partitionnement basée sur l'émetteur (garantit l'ordre pour un agent)
            key = event.source_id
            
            future = self.producer.send(topic, key=key, value=payload_dict)
            self.producer.flush()
            record_metadata = future.get(timeout=10)
            logger.debug(f"Événement {event.event_id} publié sur {record_metadata.topic} partition {record_metadata.partition}")
            return record_metadata
        except KafkaError as e:
            logger.error(f"Erreur lors de la publication de l'événement {event.event_id}: {e}")
            raise e

    def close(self):
        """Ferme la connexion proprement."""
        self.producer.flush()
        self.producer.close()
