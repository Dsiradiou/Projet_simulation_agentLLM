"""Contrat d'événements (F4) : enveloppe commune, types d'événements et leur (dé)sérialisation.

Chaque type d'événement est une classe Pydantic qui fixe son nom (`type`), sa version de schéma
(`schema_version`), le topic Kafka où il circule et la forme de sa charge utile (`payload`).
Un événement reçu qui ne correspond à aucun schéma connu est rejeté par `decode` : le
consommateur l'isole alors dans `sim.dlq` sans arrêter la simulation (voir `bus.consumer`).

Granularité (C1) : un événement par fait observable du monde (une décision, un message, un
déplacement, un changement de météo ou de stock, une valeur d'indicateur), jamais un instantané
complet de l'état à chaque pas. C'est la plus petite unité qui suffit à rejouer un run (E3) et à
remonter d'un indicateur aux faits qui l'ont produit (F6).
"""

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from agents.roles import Palier
from bus.topics import Topic
from sim.domaine import Action, Canal, Produit, Sens, SimMinute, Zone


class _Strict(BaseModel):
    # Une clé inconnue est une erreur : un événement mal formé ne passe pas inaperçu.
    model_config = ConfigDict(extra="forbid", frozen=True)


AgentId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
        description="Identifiant d'agent (ex. pharmacien_01) ou de composant (ex. horloge).",
    ),
]
TexteCourt = Annotated[str, Field(min_length=1, max_length=500)]


# --- Enveloppe commune ----------------------------------------------------------------------


class Envelope(_Strict):
    """Champs communs à tous les événements. Chaque type concret fixe `type`, `schema_version`
    et `payload`, ainsi que les constantes de classe `TOPIC` et `DESCRIPTION`."""

    TOPIC: ClassVar[Topic]
    DESCRIPTION: ClassVar[str]

    event_id: UUID = Field(default_factory=uuid4, description="Identifiant unique.")
    schema_version: str = Field(description="Version du schéma de ce type d'événement.")
    run_id: str = Field(min_length=1, max_length=64, description="Run qui a produit l'événement.")
    type: str = Field(description="Type d'événement, qui détermine la forme du payload.")
    sim_time_occurred: SimMinute = Field(description="Moment simulé où le fait se produit.")
    sim_time_processed: SimMinute = Field(
        description="Moment simulé où l'émetteur a fini de le traiter (≥ sim_time_occurred)."
    )
    wall_time_emitted: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Horodatage réel d'émission, en UTC.",
    )
    source_agent_id: AgentId = Field(description="Émetteur : agent ou composant.")
    zone: Zone | None = Field(default=None, description="Zone concernée, si elle existe.")
    caused_by: tuple[UUID, ...] = Field(
        default=(), description="Événements à l'origine de celui-ci (traçabilité, F6)."
    )
    payload: BaseModel

    @model_validator(mode="after")
    def _check_envelope(self) -> Self:
        if self.sim_time_processed < self.sim_time_occurred:
            raise ValueError("sim_time_processed est antérieur à sim_time_occurred")
        if self.event_id in self.caused_by:
            raise ValueError("un événement ne peut pas être sa propre cause")
        if self.wall_time_emitted.tzinfo is None:
            raise ValueError("wall_time_emitted doit porter un fuseau horaire")
        return self


# --- sim.world.events -----------------------------------------------------------------------


class Perturbation(StrEnum):
    """Événements que l'observateur peut injecter en direct (D3). Les quatre dernières natures
    arrivent avec la version 1.1 des schémas `monde.perturbation` et `controle.injection`."""

    RUPTURE_STOCK = "rupture_stock"
    POLEMIQUE = "polemique"
    VAGUE_CHALEUR = "vague_chaleur"
    PROMOTION_CONCURRENTE = "promotion_concurrente"
    ARTICLE_PRESSE = "article_presse"
    PUBLICATION_VIRALE = "publication_virale"
    ORAGE = "orage"


# Versions de schéma : un type évolue en ajoutant une version, et les historiques déjà enregistrés
# dans une version antérieure restent lisibles (rejeu, E3). Une version n'est jamais retirée.
VERSIONS_1_1 = Literal["1.0", "1.1"]
VERSIONS_1_2 = Literal["1.0", "1.1", "1.2"]
VERSIONS_1_3 = Literal["1.0", "1.1", "1.2", "1.3"]


class ClockStepPayload(_Strict):
    jour: PositiveInt


class ClockStep(Envelope):
    TOPIC = Topic.WORLD_EVENTS
    DESCRIPTION = "Début d'un pas de simulation (un jour). Synchronise les consommateurs."
    type: Literal["horloge.pas"] = "horloge.pas"
    schema_version: Literal["1.0"] = "1.0"
    payload: ClockStepPayload


class WeatherPayload(_Strict):
    indice_uv: Annotated[int, Field(ge=0, le=15)]
    temperature_c: Annotated[float, Field(ge=-10, le=50)]
    conditions: Literal["ensoleille", "nuageux", "pluie", "canicule"]


class Weather(Envelope):
    TOPIC = Topic.WORLD_EVENTS
    DESCRIPTION = "Météo du jour, produite par l'agent à règles météo."
    type: Literal["monde.meteo"] = "monde.meteo"
    schema_version: Literal["1.0"] = "1.0"
    payload: WeatherPayload


class StockPayload(_Strict):
    produit: Produit
    quantite: NonNegativeInt


class Stock(Envelope):
    TOPIC = Topic.WORLD_EVENTS
    DESCRIPTION = "Niveau de stock d'un produit dans une zone de vente, après changement."
    type: Literal["monde.stock"] = "monde.stock"
    schema_version: Literal["1.0"] = "1.0"
    payload: StockPayload

    @model_validator(mode="after")
    def _zone_required(self) -> Self:
        if self.zone is None:
            raise ValueError("un niveau de stock est rattaché à une zone")
        return self


class DisturbancePayload(_Strict):
    nature: Perturbation
    description: TexteCourt


class Disturbance(Envelope):
    TOPIC = Topic.WORLD_EVENTS
    DESCRIPTION = "Perturbation effective dans le monde, conséquence d'une injection (D3)."
    type: Literal["monde.perturbation"] = "monde.perturbation"
    schema_version: VERSIONS_1_1 = "1.1"  # 1.1 : nouvelles natures de perturbation
    payload: DisturbancePayload


# --- sim.agent.messages ---------------------------------------------------------------------


class AgentMessagePayload(_Strict):
    canal: Canal
    destinataires: tuple[AgentId, ...] = ()
    contenu: TexteCourt
    livraison: SimMinute = Field(
        description="Moment simulé à partir duquel le message est lisible (délai de livraison)."
    )


class AgentMessage(Envelope):
    TOPIC = Topic.AGENT_MESSAGES
    DESCRIPTION = "Message en langage naturel émis par un agent (A2)."
    type: Literal["agent.message"] = "agent.message"
    schema_version: Literal["1.0"] = "1.0"
    payload: AgentMessagePayload

    @model_validator(mode="after")
    def _check_message(self) -> Self:
        if self.payload.livraison < self.sim_time_processed:
            raise ValueError("un message ne peut pas être livré avant d'avoir été émis")
        if self.payload.canal is Canal.DIRECT and not self.payload.destinataires:
            raise ValueError("un message direct a au moins un destinataire")
        if self.payload.canal is not Canal.DIRECT and self.payload.destinataires:
            raise ValueError("seul un message direct nomme ses destinataires")
        if self.payload.canal is Canal.ZONE and self.zone is None:
            raise ValueError("un message de zone indique sa zone")
        return self


# --- sim.agent.decisions --------------------------------------------------------------------


class Moteur(StrEnum):
    LLM = "llm"  # décision produite par le modèle
    REGLE = "regle"  # agent à règles
    REPLI = "repli"  # agent LLM retombé sur sa règle après échec (A4)


class Motif(StrEnum):
    """Pourquoi un agent reconsidère sa décision ce jour-là (règle 10 de CLAUDE.md)."""

    PREMIERE_DECISION = "premiere_decision"
    MESSAGE = "message"  # message reçu
    PERTURBATION = "perturbation"  # perturbation dans sa zone
    EXPOSITION = "exposition"  # la part de porteurs autour de lui a nettement changé
    METEO = "meteo"  # début d'une journée de canicule ou d'UV fort
    LASSITUDE = "lassitude"  # pas reconsidéré depuis longtemps
    FLACON_VIDE = "flacon_vide"  # son flacon est fini : racheter ou non (version 1.3)


class LlmCall(_Strict):
    """Coût d'une décision LLM (E4), tentatives échouées comprises."""

    modele: str = Field(min_length=1)
    tokens_entree: NonNegativeInt
    tokens_sortie: NonNegativeInt
    duree_s: NonNegativeFloat
    tentatives: PositiveInt = 1


class AgentDecisionPayload(_Strict):
    action: Action
    sens: Sens
    justification: TexteCourt
    porteur: bool = Field(description="L'agent utilise le produit à l'issue de cette décision.")
    moteur: Moteur
    palier: Palier | None = None
    appel_llm: LlmCall | None = None
    motif: Motif | None = Field(
        default=None, description="Déclencheur de la décision (version 1.1 et suivantes)."
    )
    sources: tuple[Annotated[str, Field(max_length=120)], ...] = Field(
        default=(),
        description="Passages du corpus et souvenirs montrés à l'agent (version 1.2, F1, F2).",
    )


class AgentDecision(Envelope):
    TOPIC = Topic.AGENT_DECISIONS
    DESCRIPTION = "Décision d'un agent : action, sens attribué au produit, justification (D2)."
    type: Literal["agent.decision"] = "agent.decision"
    schema_version: VERSIONS_1_3 = "1.3"  # 1.1 : motif ; 1.2 : sources ; 1.3 : flacon vide
    payload: AgentDecisionPayload

    @model_validator(mode="after")
    def _check_engine(self) -> Self:
        p = self.payload
        if p.moteur is Moteur.REGLE and (p.palier or p.appel_llm):
            raise ValueError("un agent à règles n'a ni palier ni appel LLM")
        if p.moteur is not Moteur.REGLE and p.palier is None:
            raise ValueError("une décision d'agent LLM indique son palier")
        if p.moteur is Moteur.LLM and p.appel_llm is None:
            raise ValueError("une décision LLM indique le coût de son appel")
        return self


class AgentMovePayload(_Strict):
    depuis: Zone
    vers: Zone


class AgentMove(Envelope):
    TOPIC = Topic.AGENT_DECISIONS
    DESCRIPTION = "Déplacement d'un agent d'une zone à une autre (affiché dans Godot)."
    type: Literal["agent.deplacement"] = "agent.deplacement"
    schema_version: Literal["1.0"] = "1.0"
    payload: AgentMovePayload

    @model_validator(mode="after")
    def _check_move(self) -> Self:
        if self.payload.depuis is self.payload.vers:
            raise ValueError("un déplacement change de zone")
        if self.zone is not self.payload.vers:
            raise ValueError("la zone d'un déplacement est la zone d'arrivée")
        return self


# --- sim.indicators -------------------------------------------------------------------------


class IndicatorPayload(_Strict):
    nom: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
    valeur: float
    groupe: str | None = Field(default=None, description="Groupe concerné, ou None si global.")
    fenetre_debut: SimMinute
    fenetre_fin: SimMinute


class Indicator(Envelope):
    TOPIC = Topic.INDICATORS
    DESCRIPTION = "Valeur d'un indicateur calculée pendant le run ; caused_by liste ses sources."
    type: Literal["indicateur.valeur"] = "indicateur.valeur"
    schema_version: Literal["1.0"] = "1.0"
    payload: IndicatorPayload

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.payload.fenetre_fin < self.payload.fenetre_debut:
            raise ValueError("la fenêtre se termine avant de commencer")
        return self


# --- sim.control ----------------------------------------------------------------------------


class InjectionPayload(_Strict):
    nature: Perturbation
    description: TexteCourt


class Injection(Envelope):
    TOPIC = Topic.CONTROL
    DESCRIPTION = "Demande d'injection d'une perturbation depuis l'interface (D3)."
    type: Literal["controle.injection"] = "controle.injection"
    schema_version: VERSIONS_1_1 = "1.1"  # 1.1 : nouvelles natures de perturbation
    payload: InjectionPayload


class CommandPayload(_Strict):
    commande: Literal["pause", "reprise", "arret"]


class Command(Envelope):
    TOPIC = Topic.CONTROL
    DESCRIPTION = "Commande de pilotage du run depuis l'interface."
    type: Literal["controle.commande"] = "controle.commande"
    schema_version: Literal["1.0"] = "1.0"
    payload: CommandPayload


# --- sim.incidents --------------------------------------------------------------------------


class NatureIncident(StrEnum):
    SORTIE_INVALIDE = "sortie_invalide"  # réponse LLM non conforme au schéma JSON
    HORS_ROLE = "hors_role"  # réponse conforme mais incohérente avec le profil
    SANS_REPONSE = "sans_reponse"  # délai dépassé
    REPLI = "repli"  # bascule sur la règle après échec de la relance
    VIOLATION_TEMPORELLE = "violation_temporelle"  # information lue avant d'être disponible (C6)


class IncidentPayload(_Strict):
    nature: NatureIncident
    agent_id: AgentId | None = None
    detail: Annotated[str, Field(min_length=1, max_length=2000)]
    tentative: NonNegativeInt = 0


class Incident(Envelope):
    TOPIC = Topic.INCIDENTS
    DESCRIPTION = "Incident d'agent ou de cohérence temporelle, tracé et compté (A4, C6)."
    type: Literal["incident"] = "incident"
    schema_version: Literal["1.0"] = "1.0"
    payload: IncidentPayload


# --- sim.dlq --------------------------------------------------------------------------------

DLQ_RAW_MAX = 10_000


class RejectionPayload(_Strict):
    topic_origine: str
    brut: Annotated[str, Field(max_length=DLQ_RAW_MAX)]
    erreur: Annotated[str, Field(min_length=1)]


class Rejection(Envelope):
    TOPIC = Topic.DLQ
    DESCRIPTION = "Message reçu non conforme à un schéma, isolé sans arrêter la simulation (F4)."
    type: Literal["dlq.rejet"] = "dlq.rejet"
    schema_version: Literal["1.0"] = "1.0"
    payload: RejectionPayload


# --- Registre et (dé)sérialisation -----------------------------------------------------------

EVENT_CLASSES: tuple[type[Envelope], ...] = (
    ClockStep,
    Weather,
    Stock,
    Disturbance,
    AgentMessage,
    AgentDecision,
    AgentMove,
    Indicator,
    Injection,
    Command,
    Incident,
    Rejection,
)

Event = Annotated[
    ClockStep
    | Weather
    | Stock
    | Disturbance
    | AgentMessage
    | AgentDecision
    | AgentMove
    | Indicator
    | Injection
    | Command
    | Incident
    | Rejection,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class EventValidationError(ValueError):
    """Message qui ne respecte aucun schéma d'événement connu."""


# Les valeurs par défaut (event_id, horodatage...) servent à construire un événement. Un message
# reçu, lui, doit tout fournir : sinon decode inventerait un identifiant et casserait la
# traçabilité. Seuls `zone` et `caused_by` sont facultatifs.
WIRE_REQUIRED = frozenset(Envelope.model_fields) - {"zone", "caused_by"}


def encode(event: Envelope) -> bytes:
    return event.model_dump_json().encode("utf-8")


def decode(raw: bytes | str) -> Event:
    """Valide un message reçu. Lève EventValidationError s'il n'est pas conforme."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EventValidationError(f"JSON illisible : {exc}") from exc
    if not isinstance(data, dict):
        raise EventValidationError("un événement est un objet JSON")
    missing = sorted(WIRE_REQUIRED - data.keys())
    if missing:
        raise EventValidationError(f"champs obligatoires absents : {', '.join(missing)}")
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError as exc:
        # Chemin du champ fautif sans le nom interne de l'union : « agent.decision.payload.sens ».
        details = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)
        ]
        raise EventValidationError("; ".join(details)) from exc


def topic_for(event: Envelope) -> Topic:
    return type(event).TOPIC
