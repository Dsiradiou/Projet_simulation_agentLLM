"""Passerelle WebSocket bidirectionnelle entre Kafka et Godot Engine.
- Sens Kafka -> Godot (D1, B2) : Pousse les événements de déplacement, décision et métriques.
- Sens Godot -> Kafka (D3) : Reçoit les injections d'événements utilisateur et les publie sur Kafka.
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time
from typing import Set

# Ajout de la racine du projet au PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import websockets

from src.config import KAFKA_BOOTSTRAP_SERVERS, TOPIC_EVENTS, TOPIC_METRICS
from src.schemas.events import BaseSimulationEvent, EventType
from src.streaming.consumer import SimulationEventConsumer
from src.streaming.producer import SimulationEventProducer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("GodotGateway")

WS_PORT = 8765
WS_HOST = "0.0.0.0"


class GodotWebSocketGateway:
    def __init__(self, host: str = WS_HOST, port: int = WS_PORT):
        self.host = host
        self.port = port
        self.connected_clients: Set[any] = set()
        self.producer = SimulationEventProducer()
        self.loop = None
        self.running = False

    async def register_client(self, websocket):
        """Enregistre un nouveau client Godot connecté."""
        self.connected_clients.add(websocket)
        logger.info(f"[Godot] Nouveau client connecté depuis {websocket.remote_address}. Total: {len(self.connected_clients)}")
        
        # Message de bienvenue et confirmation de protocole
        welcome_msg = {
            "type": "GATEWAY_CONNECTED",
            "message": "Connecté à la passerelle Kafka-Godot de Mondes Vivants",
            "timestamp": time.time()
        }
        await websocket.send(json.dumps(welcome_msg))
        
        try:
            # Écoute les messages venant de Godot (Ex: Injection d'événement D3)
            async for raw_message in websocket:
                await self.handle_godot_message(raw_message, websocket)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[Godot] Client déconnecté {websocket.remote_address}")
        finally:
            self.connected_clients.remove(websocket)

    async def handle_godot_message(self, raw_message: str, websocket):
        """Traite les actions initiées depuis l'interface Godot (D3)."""
        try:
            data = json.loads(raw_message)
            msg_type = data.get("type")
            
            if msg_type == "INJECT_EVENT":
                # Exigence D3 : Injection d'un événement depuis l'interface Godot
                logger.info(f"[D3 - Injection Godot] Réception perturbation : {data.get('event_type')}")
                
                event = BaseSimulationEvent(
                    event_type=EventType.ANNOUNCEMENT_INJECTED,
                    simulated_step=data.get("simulated_step", 0),
                    simulated_time_str=data.get("simulated_time_str", "Temps Manuel"),
                    source_id="godot_user_interface",
                    location=data.get("location", "plage"),
                    payload=data.get("payload", {})
                )
                
                # Publication sur Kafka pour que la simulation l'ingère
                self.producer.emit_event(event)
                logger.info(f"[D3] Événement injecté publié sur Kafka avec succès (ID: {event.event_id})")

        except Exception as e:
            logger.error(f"Erreur lors du traitement d'un message Godot: {e}")

    def broadcast_to_godot(self, event: BaseSimulationEvent):
        """Envoie un événement Kafka vers tous les clients Godot connectés (thread-safe)."""
        if not self.connected_clients or not self.loop:
            return
            
        message = {
            "type": "SIMULATION_EVENT",
            "event_type": event.event_type.value,
            "source_id": event.source_id,
            "location": event.location,
            "simulated_step": event.simulated_step,
            "simulated_time_str": event.simulated_time_str,
            "real_timestamp": event.real_timestamp,
            "payload": event.payload
        }
        json_str = json.dumps(message)
        
        # Planifie l'envoi dans la boucle événementielle asyncio
        for client in list(self.connected_clients):
            asyncio.run_coroutine_threadsafe(self._safe_send(client, json_str), self.loop)

    async def _safe_send(self, client, message: str):
        try:
            await client.send(message)
        except Exception as e:
            logger.warning(f"Échec envoi vers client Godot: {e}")

    def _kafka_consumer_worker(self):
        """Thread d'écoute continue des événements Kafka."""
        logger.info("Démarrage du thread de consommation Kafka...")
        consumer = SimulationEventConsumer(
            topics=[TOPIC_EVENTS, TOPIC_METRICS],
            group_id="godot-gateway-group",
            auto_offset_reset="latest"
        )
        
        def on_event(event: BaseSimulationEvent):
            self.broadcast_to_godot(event)

        # Consomme en continu
        consumer.consume(on_event_callback=on_event)

    async def start_server(self):
        """Démarre le serveur WebSocket et le thread Kafka."""
        self.loop = asyncio.get_running_loop()
        self.running = True
        
        # Lance le consommateur Kafka dans un thread dédié
        kafka_thread = threading.Thread(target=self._kafka_consumer_worker, daemon=True)
        kafka_thread.start()
        
        logger.info(f"Serveur WebSocket pour Godot prêt sur ws://{self.host}:{self.port}")
        async with websockets.serve(self.register_client, self.host, self.port):
            await asyncio.Future() # Maintient le serveur actif


def main():
    gateway = GodotWebSocketGateway()
    try:
        asyncio.run(gateway.start_server())
    except KeyboardInterrupt:
        logger.info("Passerelle arrêtée.")


if __name__ == "__main__":
    main()
