"""Contrat d'événements Kafka versionné et typé avec Pydantic.
Répond aux exigences C3 (horodatage double simulé/réel) et F4 (schéma documenté et versionné).
"""
from enum import Enum
from typing import Any, Dict, Optional
import time
import uuid
from pydantic import BaseModel, Field


class EventType(str, Enum):
    # Horloge & cycle
    CLOCK_TICK = "CLOCK_TICK"
    
    # Cycle de vie et mouvement de l'agent
    AGENT_MOVE = "AGENT_MOVE"
    
    # Interactions sociales et décision
    AGENT_DIALOGUE = "AGENT_DIALOGUE"
    AGENT_DECISION = "AGENT_DECISION"
    AGENT_OPINION = "AGENT_OPINION"
    
    # Actions commerciales et produit
    PRODUCT_PURCHASE = "PRODUCT_PURCHASE"
    PRODUCT_USAGE = "PRODUCT_USAGE"
    PRODUCT_ABANDON = "PRODUCT_ABANDON"
    
    # Environnement & perturbations externes (D3)
    MARKET_RESTOCK = "MARKET_RESTOCK"
    MARKET_SHORTAGE = "MARKET_SHORTAGE"
    WEATHER_CHANGE = "WEATHER_CHANGE"
    ANNOUNCEMENT_INJECTED = "ANNOUNCEMENT_INJECTED"


class BaseSimulationEvent(BaseModel):
    """Schéma de base obligatoire pour tout événement transitant sur Kafka."""
    schema_version: str = Field(default="1.0.0", description="Version du contrat d'événement (F4)")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Identifiant unique UUID")
    event_type: EventType = Field(..., description="Type de l'événement émis")
    
    # Exigence C3 : distinction explicite entre temps simulé et temps réel
    simulated_step: int = Field(..., description="Pas de temps simulé (discret)")
    simulated_time_str: str = Field(..., description="Représentation lisible (ex: 'J3 · 14:30')")
    real_timestamp: float = Field(default_factory=time.time, description="Timestamp POSIX réel de création")
    processed_timestamp: Optional[float] = Field(default=None, description="Timestamp réel de traitement/consommation")

    # Émetteur de l'événement (Agent, Horloge, Système ou Utilisateur)
    source_id: str = Field(..., description="ID de l'émetteur (ex: 'agent_01', 'clock_system', 'user_cli')")
    location: Optional[str] = Field(default=None, description="Zone où l'événement a lieu")
    
    # Données spécifiques à l'événement
    payload: Dict[str, Any] = Field(default_factory=dict, description="Charge utile spécifique")


class AgentDecisionPayload(BaseModel):
    """Charge utile d'une décision d'agent (LLM ou règles)."""
    current_opinion: str = Field(..., description="Catégorie de sens: efficace, tendance, banal, depasse")
    has_product: bool = Field(..., description="L'agent possède-t-il le flacon Novalis ?")
    action_taken: str = Field(..., description="Action concrète: acheter, appliquer, recommander, moquer, délaisser")
    reasoning: Optional[str] = Field(default=None, description="Justification ou explication (D2)")
    engine_used: str = Field(..., description="Moteur de décision: 'llm_grand', 'llm_moyen', 'llm_petit', 'regles'")
    tokens_used: int = Field(default=0, description="Coût en tokens pour cette décision (E4)")
