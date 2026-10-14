"""Consommateur Kafka pour la simulation Mondes Vivants.
Reçoit les événements, valide les schémas et isole les événements non conformes (F4).
Met à jour le timestamp de traitement réel (C3).
"""
import json
import logging
import time
from typing import Callable, Optional, List
from kafka import KafkaConsumer
from pydantic import ValidationError

from src.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_EVENTS
from src.schemas.events import BaseSimulationEvent

logger = logging.getLogger(__name__)


class SimulationEventConsumer:
    def __init__(
        self,
        topics: Optional[List[str]] = None,
        group_id: str = "simulation-monitor-group",
        bootstrap_servers: Optional[str] = None,
        auto_offset_reset: str = "earliest"
    ):
        self.bootstrap_servers = bootstrap_servers or KAFKA_BOOTSTRAP_SERVERS
        self.topics = topics or [TOPIC_EVENTS]
        self.consumer = KafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=group_id,
            auto_offset_reset=auto_offset_reset,
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            key_deserializer=lambda k: k.decode("utf-8") if k else None
        )
        # File d'attente pour événements corrompus ou invalides (Dead Letter Queue interne)
        self.invalid_events_quarantine: List[dict] = []
        logger.info(f"SimulationEventConsumer abonné aux topics {self.topics}")

    def consume(self, on_event_callback: Callable[[BaseSimulationEvent], None], max_messages: Optional[int] = None):
        """Boucle de consommation avec validation Pydantic et gestion d'erreurs (F4, C3)."""
        count = 0
        try:
            for message in self.consumer:
                raw_data = message.value
                count += 1
                try:
                    # Validation stricte du contrat Pydantic (F4)
                    event = BaseSimulationEvent.model_validate(raw_data)
                    
                    # Exigence C3 : horodatage réel de traitement
                    event.processed_timestamp = time.time()
                    
                    # Appel du traitement métier
                    on_event_callback(event)
                        
                except ValidationError as ve:
                    # Exigence F4 : isolement sans blocage
                    logger.warning(f"[F4 - Alerte Schéma] Événement non conforme isolé : {ve.errors()} | Raw: {raw_data}")
                    self.invalid_events_quarantine.append({
                        "raw_data": raw_data,
                        "error": str(ve),
                        "timestamp": time.time()
                    })
                
                if max_messages and count >= max_messages:
                    break
        except KeyboardInterrupt:
            logger.info("Consommation interrompue par l'utilisateur.")
        finally:
            self.close()

    def close(self):
        """Ferme la connexion proprement."""
        self.consumer.close()
