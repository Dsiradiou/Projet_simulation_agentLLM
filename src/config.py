"""Configuration globale de la simulation Mondes Vivants (Sujet 04 : Éphémère).
"""
from typing import List

# Paramètres de streaming Kafka
KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
TOPIC_EVENTS: str = "simulation.events"
TOPIC_TICK: str = "simulation.clock"
TOPIC_METRICS: str = "simulation.metrics"

# Les 7 zones validées du monde
ZONES: List[str] = [
    "pharmacie",
    "plage",
    "piscine",
    "reseaux_sociaux",
    "centre_commercial",
    "terrain_sport",
    "domicile"
]

# Les 4 catégories de sens fermées imposées (Exigence B1, F4)
SENS_CATEGORIES: List[str] = [
    "efficace",
    "tendance",
    "banal",
    "depasse"
]

# Nom fictif du produit et ingrédients inventés (Évite toute contamination LLM)
PRODUCT_NAME: str = "Novalis"
PRODUCT_SPF: int = 42
ACTIVE_INGREDIENT: str = "Phyto-Kératine Z3"
