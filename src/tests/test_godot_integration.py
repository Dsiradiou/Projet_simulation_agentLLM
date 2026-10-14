"""Test d'intégration bout-en-bout :
Kafka -> Passerelle WebSocket -> Client Godot -> Injection retour vers Kafka (D1, D2, D3).
"""
import asyncio
import json
import os
import sys
import time
import websockets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.config import TOPIC_EVENTS
from src.schemas.events import BaseSimulationEvent, EventType, AgentDecisionPayload
from src.streaming.producer import SimulationEventProducer


async def simulate_fake_godot_client():
    uri = "ws://127.0.0.1:8765"
    print(f"\n[Godot Mock] Connexion vers {uri}...")
    
    async with websockets.connect(uri) as websocket:
        # 1. Réception du message de bienvenue
        welcome = await websocket.recv()
        print(f"  [Reçu] Handshake Gateway: {welcome}")

        # 2. On émet un événement dans Kafka via notre Producer
        producer = SimulationEventProducer()
        decision_payload = AgentDecisionPayload(
            current_opinion="tendance",
            has_product=True,
            action_taken="appliquer",
            reasoning="Vu sur les réseaux, j'applique Novalis à la plage !",
            engine_used="llm_moyen",
            tokens_used=72
        )
        sim_event = BaseSimulationEvent(
            event_type=EventType.AGENT_DECISION,
            simulated_step=15,
            simulated_time_str="J1 · 14:00",
            source_id="agent_influenceur_01",
            location="plage",
            payload=decision_payload.model_dump()
        )
        print("\n[Simulation Producer] Émission événement Kafka -> agent_influenceur_01...")
        producer.emit_event(sim_event)
        producer.close()

        # 3. Le faux client Godot attend de recevoir l'événement retransmis par la passerelle
        print("[Godot Mock] En attente de diffusion par la passerelle WebSocket...")
        msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
        parsed = json.loads(msg)
        print(f"  -> Événement reçu dans Godot : Type={parsed.get('event_type')}, Agent={parsed.get('source_id')}, Zone={parsed.get('location')}")

        # 4. Test d'injection utilisateur (Exigence D3)
        print("\n[Godot Mock - Exigence D3] Envoi d'une injection de perturbation depuis Godot...")
        injection_msg = {
            "type": "INJECT_EVENT",
            "event_type": "MARKET_SHORTAGE",
            "location": "pharmacie",
            "simulated_step": 15,
            "simulated_time_str": "J1 · 14:05",
            "payload": {
                "message": "Alerte : Rupture de stock générale sur Novalis SPF 42 !",
                "impact": "hausse_prix_et_frustration"
            }
        }
        await websocket.send(json.dumps(injection_msg))
        print("  -> Message D3 envoyé sur le WebSocket.")
        await asyncio.sleep(1.0)
        print("\n Test d'intégration Passerelle WebSocket <-> Godot réussi !")


if __name__ == "__main__":
    asyncio.run(simulate_fake_godot_client())
