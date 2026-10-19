"""Interface commune des agents, à règles ou LLM, telle que l'orchestrateur la voit.

Un agent ne lit jamais Kafka ni Redis lui-même : l'orchestrateur lui donne une `Perception`
(ce qu'il peut savoir à ce moment simulé, et rien d'autre) et publie sa `Decision`.
"""

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID

from agents.roles import Palier, Role, palier_du_role
from bus.events import (
    AgentMessage,
    Envelope,
    LlmCall,
    Moteur,
    Motif,
    NatureIncident,
    WeatherPayload,
)
from sim.config import DeclencheursConfig
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
class IncidentAgent:
    """Incident survenu pendant une décision (relance, repli...). L'agent ne publie rien lui-même :
    l'orchestrateur le publie sur `sim.incidents`, rattaché à la décision (A4, F6)."""

    nature: NatureIncident
    detail: str
    tentative: int = 0


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
    incidents: tuple[IncidentAgent, ...] = ()
    sources: tuple[str, ...] = ()  # passages du corpus et souvenirs montrés au modèle (RAG)


def porteur_apres(action: Action, porteur_avant: bool) -> bool:
    """L'agent utilise-t-il le produit après cette action ? Acheter ou appliquer en fait un
    porteur, abandonner l'en retire ; les autres actions ne changent rien."""
    if action in (Action.ACHETER, Action.APPLIQUER):
        return True
    if action is Action.ABANDONNER:
        return False
    return porteur_avant


class Agent(ABC):
    def __init__(self, agent_id: str, role: Role, zone: Zone, rng: random.Random) -> None:
        self.agent_id = agent_id
        self.role = role
        self.palier: Palier | None = palier_du_role(role)
        self.zone = zone
        self.rng = rng
        self.derniere_decision: Decision | None = None
        # Comptes suivis sur les réseaux ; None : reçoit toutes les publications (agents de test).
        self.abonnements: tuple[str, ...] | None = None
        # Déclencheurs supplémentaires (configuration) ; None : seulement messages et perturbations.
        self.declencheurs: DeclencheursConfig | None = None
        self.motif: Motif | None = None  # motif du jour, fixé par `doit_decider`
        self._part_vue = 0.0  # part de porteurs autour de lui lors de sa dernière décision
        self._jour_decision = 0
        self._delai = 0  # délai de réflexion tiré après la dernière décision
        self._meteo_forte_veille = False

    @property
    def porteur(self) -> bool:
        return self.derniere_decision.porteur if self.derniere_decision else False

    @property
    def sens(self) -> Sens | None:
        return self.derniere_decision.sens if self.derniere_decision else None

    def doit_decider(self, perception: Perception) -> bool:
        """Déclencheur (règle 10 de CLAUDE.md) : sans changement de situation, l'agent garde sa
        décision précédente et ne coûte aucun appel. Appelé une fois par jour pour chaque agent ;
        le motif retenu est gardé dans `motif` et publié avec la décision (F6)."""
        self.motif = self._motif(perception)
        return self.motif is not None

    def _motif(self, p: Perception) -> Motif | None:
        d = self.declencheurs
        m = p.meteo
        forte = bool(d and m and (m.conditions == "canicule" or m.indice_uv >= d.meteo_uv_fort))
        meteo_nouvelle, self._meteo_forte_veille = forte and not self._meteo_forte_veille, forte
        if self.derniere_decision is None:
            return Motif.PREMIERE_DECISION
        if p.evenements:
            return Motif.PERTURBATION
        if d is not None and p.jour - self._jour_decision < self._delai:
            return None  # délai de réflexion : les messages attendent la prochaine décision
        if p.messages:
            return Motif.MESSAGE
        if d is None:
            return None
        if meteo_nouvelle:
            return Motif.METEO
        if abs(p.part_porteurs_zone - self._part_vue) >= d.exposition_ecart:
            return Motif.EXPOSITION
        if p.jour - self._jour_decision >= d.lassitude_jours:
            return Motif.LASSITUDE
        return None

    def noter_decision(self, decision: Decision, perception: Perception) -> None:
        """Appelé par l'orchestrateur quand la décision est publiée : point de départ des
        déclencheurs d'exposition et de lassitude."""
        self.derniere_decision = decision
        self._part_vue = perception.part_porteurs_zone
        self._jour_decision = perception.jour
        if self.declencheurs:  # tiré avec le générateur de l'agent : reproductible, désynchronisé
            d = self.declencheurs
            ecart = d.delai_reflexion_ecart
            self._delai = d.delai_reflexion_jours + self.rng.randint(-ecart, ecart)

    @abstractmethod
    async def decider(self, perception: Perception) -> Decision: ...


class WorldActor(ABC):
    """Acteur à règles qui produit des événements du monde en début de journée (météo...)."""

    actor_id: str

    @abstractmethod
    def evenements_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]: ...
