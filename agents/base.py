"""Interface commune des agents, à règles ou LLM, telle que l'orchestrateur la voit.

Un agent ne lit jamais Kafka ni Redis lui-même : l'orchestrateur lui donne une `Perception`
(ce qu'il peut savoir à ce moment simulé, et rien d'autre) et publie sa `Decision`.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID

from agents.roles import Palier, Role, palier_du_role
from bus.events import AgentMessage, Envelope, LlmCall, Moteur, WeatherPayload
from sim.domaine import Action, Canal, Sens, Zone


@dataclass(frozen=True)
class Perception:
    """Ce qu'un agent sait au moment de décider. Construite par l'orchestrateur, qui garantit
    qu'elle ne contient que des informations déjà disponibles (C6)."""

    jour: int
    minute: int
    zone: Zone
    messages: tuple[AgentMessage, ...] = ()  # messages livrés depuis la dernière décision
    evenements: tuple[Envelope, ...] = ()  # perturbations du monde survenues depuis
    meteo: WeatherPayload | None = None
    part_porteurs_zone: float = 0.0  # part de porteurs dans sa zone, état de la veille


@dataclass(frozen=True)
class MessageIntent:
    """Message que l'agent veut envoyer. L'orchestrateur fixe sa date de livraison."""

    canal: Canal
    contenu: str
    destinataires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    action: Action
    sens: Sens
    justification: str
    porteur: bool
    moteur: Moteur
    palier: Palier | None = None
    appel_llm: LlmCall | None = None
    messages: tuple[MessageIntent, ...] = ()
    deplacement: Zone | None = None
    caused_by: tuple[UUID, ...] = field(default=())  # événements de la perception utilisés


class Agent(ABC):
    def __init__(self, agent_id: str, role: Role, zone: Zone, rng: random.Random) -> None:
        self.agent_id = agent_id
        self.role = role
        self.palier: Palier | None = palier_du_role(role)
        self.zone = zone
        self.rng = rng
        self.derniere_decision: Decision | None = None

    @property
    def porteur(self) -> bool:
        return self.derniere_decision.porteur if self.derniere_decision else False

    @property
    def sens(self) -> Sens | None:
        return self.derniere_decision.sens if self.derniere_decision else None

    def doit_decider(self, perception: Perception) -> bool:
        """Déclencheur (règle 10 de CLAUDE.md) : sans changement de situation, l'agent garde sa
        décision précédente et ne coûte aucun appel. Les sous-classes peuvent l'enrichir."""
        return (
            self.derniere_decision is None
            or bool(perception.messages)
            or bool(perception.evenements)
        )

    @abstractmethod
    async def decider(self, perception: Perception) -> Decision: ...


class WorldActor(ABC):
    """Acteur à règles qui produit des événements du monde en début de journée (météo...)."""

    actor_id: str

    @abstractmethod
    def evenements_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]: ...
