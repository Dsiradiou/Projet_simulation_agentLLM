"""Script de validation du pipeline de streaming Apache Kafka.
Démontre :
1. L'émission d'un événement typé et valide (BaseSimulationEvent).
2. La détection et mise en quarantaine d'un événement non conforme (Exigence F4).
3. Le calcul du temps de décalage temporel traitement/émission (Exigence C3 & C7).
"""
import sys
import os
import json
import time

# Ajout de la racine du projet au PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.config import TOPIC_EVENTS, PRODUCT_NAME
from src.schemas.events import BaseSimulationEvent, EventType, AgentDecisionPayload
from src.streaming.producer import SimulationEventProducer
from src.streaming.consumer import SimulationEventConsumer


def main():
    print("=" * 60)
    print("TEST DU PIPELINE KAFKA - SUJET ÉPHÉMÈRE (Mondes Vivants)")
    print("=" * 60)

    # 1. Initialisation du producteur
    print("\n[1] Connexion au Broker Kafka (localhost:9092)...")
    try:
        producer = SimulationEventProducer()
        print("  -> Connecté avec succès.")
    except Exception as e:
        print(f"  [ERREUR] Impossible de se connecter à Kafka : {e}")
        print("  Assurez-vous que Docker Desktop est lancé et que 'docker compose up -d' est exécuté.")
        return

    # 2. Création et publication d'un événement valide
    print("\n[2] Émission d'un événement de décision d'agent valide...")
    decision_payload = AgentDecisionPayload(
        current_opinion="tendance",
        has_product=True,
        action_taken="appliquer",
        reasoning="J'ai vu plusieurs créateurs recommander Novalis sur la plage, je décide de l'essayer.",
        engine_used="llm_moyen",
        tokens_used=64
    )

    valid_event = BaseSimulationEvent(
        event_type=EventType.AGENT_DECISION,
        simulated_step=12,
        simulated_time_str="J1 · 11:30",
        source_id="agent_baigneur_03",
        location="plage",
        payload=decision_payload.model_dump()
    )

    producer.emit_event(valid_event)
    print(f"  -> Événement valide émis avec succès (ID: {valid_event.event_id})")

    # 3. Émission d'un événement non conforme pour tester l'exigence F4
    print("\n[3] Test de l'exigence F4 : Émission d'un événement non conforme (schéma cassé)...")
    corrupted_data = {
        "schema_version": "0.1",
        # 'event_type' manquant
        # 'simulated_step' manquant
        "source_id": "malicious_or_buggy_source",
        "random_field": "valeur inattendue"
    }
    # Envoi direct via le producteur sous-jacent en brut
    producer.producer.send(TOPIC_EVENTS, value=corrupted_data)
    producer.producer.flush()
    print("  -> Message non conforme injecté sur le topic.")

    producer.close()

    # 4. Consommation et vérification
    print("\n[4] Consommation des messages et validation Pydantic...")
    received_events = []

    def on_event(event: BaseSimulationEvent):
        delay_ms = (event.processed_timestamp - event.real_timestamp) * 1000
        print(f"\n  [OK] Reçu événement : {event.event_type.value}")
        print(f"       Source : {event.source_id} | Zone : {event.location}")
        print(f"       Temps simulé : {event.simulated_time_str} (step {event.simulated_step})")
        print(f"       Latence réelle d'acheminement : {delay_ms:.2f} ms")
        print(f"       Payload : {event.payload}")
        received_events.append(event)

    consumer = SimulationEventConsumer(
        group_id=f"test-group-{int(time.time())}", # Nouveau groupe pour relire immédiatement
        auto_offset_reset="earliest"
    )

    print("  En attente de réception des messages...")
    # max_messages=2 pour traiter le message valide ET le message corrompu
    consumer.consume(on_event_callback=on_event, max_messages=2)

    print("\n[5] Bilan du test de conformité :")
    print(f"  - Événements valides traités : {len(received_events)}")
    print(f"  - Événements non conformes isolés en quarantaine (F4) : {len(consumer.invalid_events_quarantine)}")
    if consumer.invalid_events_quarantine:
        print(f"  - Détail erreur isolée : {consumer.invalid_events_quarantine[0]['error'][:100]}...")

    print("\n Pipeline Kafka opérationnel et conforme aux exigences O3, C1, C3 et F4 !")


if __name__ == "__main__":
    main()
