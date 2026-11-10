"""Chargement et validation de la configuration d'un run (fichiers `configs/*.yaml`).

Tout paramètre de simulation passe par ce module : rien n'est écrit en dur dans le code (C2).
Une configuration invalide arrête le lancement avec un message qui indique le champ fautif.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from agents.roles import Palier, Role
from sim.domaine import ZONES_PAR_TYPE_MAX, Canal, Zone, lieux_du_monde
from sim.ville import (
    AGENTS_PAR_TAILLE,
    Plan,
    PlanVille,
    Taille,
    construire_plan,
    lieux_de_la_ville,
    plan_effectif,
    taille_effective,
)


class Mode(StrEnum):
    """Mode d'exécution, choisi au lancement (C4)."""

    SYNCHRONE = "synchrone"
    ASYNCHRONE = "asynchrone"


class _Section(BaseModel):
    # extra="forbid" : une clé mal orthographiée dans le YAML est une erreur, pas un oubli.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RunConfig(_Section):
    seed: NonNegativeInt = Field(description="Graine explicite, pour la reproductibilité.")


class AsynchroneConfig(_Section):
    """Mode asynchrone (étape 15) : pendant la journée, l'heure simulée suit le temps réel."""

    debut_min: int = Field(
        default=510, ge=0, lt=24 * 60, description="Heure où commencent les décisions (08:30)."
    )
    minutes_par_seconde: PositiveFloat = Field(
        default=15,
        description="Minutes simulées par seconde réelle de calcul : la latence d'un modèle "
        "devient du temps simulé.",
    )
    fenetre_regles_min: NonNegativeInt = Field(
        default=210,
        description="Les agents à règles décident à une heure tirée entre `debut_min` et "
        "`debut_min` + cette fenêtre (08:30 à 12:00).",
    )


class SimulationConfig(_Section):
    duree_jours: PositiveInt = Field(description="Durée simulée, un pas = un jour.")
    mode: Mode
    asynchrone: AsynchroneConfig = Field(default_factory=AsynchroneConfig)
    delais_livraison_min: dict[Canal, NonNegativeInt] = Field(
        description="Délai, en minutes simulées, avant qu'un message soit lisible, par canal."
    )
    diffusion: bool = Field(
        default=True,
        description="False : run témoin (B3), aucun message n'est remis et personne ne voit "
        "qui utilise le produit autour de lui.",
    )

    @field_validator("delais_livraison_min")
    @classmethod
    def _all_channels(cls, value: dict[Canal, int]) -> dict[Canal, int]:
        missing = set(Canal) - set(value)
        if missing:
            raise ValueError(f"délai manquant pour : {', '.join(sorted(missing))}")
        return value


class LlmConfig(_Section):
    modeles: dict[Palier, str] = Field(description="Modèle Ollama de chaque palier (A5).")
    parallelisme: dict[Palier, PositiveInt] = Field(
        description="Requêtes simultanées par palier (mesuré à l'étape 3)."
    )
    delai_max_s: PositiveFloat = Field(description="Délai d'un appel avant abandon (A4).")
    tokens_sortie_max: PositiveInt = Field(description="Plafond de tokens de la réponse.")
    contexte_tokens: PositiveInt = Field(description="Contexte, égal à OLLAMA_CONTEXT_LENGTH.")
    temperature: float = Field(ge=0, le=2)

    @field_validator("modeles", "parallelisme")
    @classmethod
    def _all_tiers(cls, value: dict[Palier, object]) -> dict[Palier, object]:
        missing = set(Palier) - set(value)
        if missing:
            raise ValueError(f"palier manquant : {', '.join(sorted(missing))}")
        return value


class SeuilConfig(_Section):
    moyenne: float = Field(ge=0, le=1)
    ecart_type: float = Field(ge=0, le=1)


class MoteurAgents(StrEnum):
    LLM = "llm"
    REGLES = "regles"


class AgentsConfig(_Section):
    moteur: MoteurAgents = MoteurAgents.LLM
    seuil_adoption: SeuilConfig = Field(
        description="Loi du seuil d'adoption individuel (Granovetter), bornée à [0, 1]."
    )
    messages_par_semaine: dict[Role, NonNegativeInt] = Field(
        description="Messages qu'un agent LLM peut envoyer par semaine simulée, par rôle."
    )


class MeteoConfig(_Section):
    temperature_debut: float = Field(ge=-10, le=50)
    temperature_plateau: float = Field(ge=-10, le=50)
    montee_jours: PositiveInt
    ecart_jour: float = Field(ge=0, le=10)
    persistance: float = Field(ge=0, lt=1)
    seuil_canicule: float = Field(ge=0, le=50)
    beau_vers_mauvais: float = Field(ge=0, le=1)
    mauvais_vers_beau: float = Field(ge=0, le=1)
    duree_vague_chaleur: PositiveInt


class CommerceConfig(_Section):
    points_de_vente: list[Zone] = Field(min_length=1)
    stock_initial_pour_100: NonNegativeInt
    reassort_pour_100: NonNegativeInt
    marge_commande: float = Field(ge=0, le=5)
    jour_reassort: int = Field(ge=0, le=6)
    duree_flacon_jours: PositiveInt = Field(
        description="Journées d'usage ordinaire que contient un flacon (une application par jour)."
    )
    usage_par_zone: dict[Zone, PositiveFloat] = Field(
        default_factory=dict,
        description="Journées d'usage consommées par un jour passé dans ce type de lieu (X4, "
        "10 nov.) : la plage et la piscine vident le flacon plus vite. 1 pour un type absent.",
    )


class ReglesConfig(_Section):
    bonus_fidelite: float = Field(ge=0, le=1)
    bonus_promotion: float = Field(ge=0, le=1)
    recul_abandon: float = Field(ge=0, le=1)
    p_externe: float = Field(ge=0, le=1)
    p_campagne: float = Field(ge=0, le=1)
    q_message: float = Field(ge=0, le=1)
    recul_mode: float = Field(ge=0, le=1)
    p_abandon_recul: float = Field(ge=0, le=1)
    p_abandon_polemique: float = Field(ge=0, le=1)


class DeclencheursConfig(_Section):
    delai_reflexion_jours: PositiveInt
    delai_reflexion_ecart: NonNegativeInt = 0

    @model_validator(mode="after")
    def _ecart_inferieur_au_delai(self) -> "DeclencheursConfig":
        if self.delai_reflexion_ecart >= self.delai_reflexion_jours:
            raise ValueError("l'écart du délai de réflexion doit rester sous le délai")
        return self

    exposition_ecart: float = Field(gt=0, le=1)
    meteo_uv_fort: int = Field(ge=0, le=15)
    # Renommé au début de l'étape 16 : « lassitude » désigne désormais l'état L de l'agent
    # (section `lassitude`). L'ancien nom reste accepté pour relire les manifestes des runs.
    relance_jours: PositiveInt = Field(
        validation_alias=AliasChoices("relance_jours", "lassitude_jours"),
        description="Un agent qui pense encore au produit y repense après ce nombre de jours.",
    )


class ReseauConfig(_Section):
    abonnements_par_agent: NonNegativeInt
    contacts_directs: NonNegativeInt
    audience: dict[Role, PositiveFloat] = Field(default_factory=dict)


class Temps(StrEnum):
    BEAU = "beau"
    MAUVAIS = "mauvais"


class RoutinesConfig(_Section):
    stabilite: float = Field(ge=0, le=1)
    roles: dict[Role, dict[Temps, dict[Zone, PositiveFloat]]]

    @field_validator("roles")
    @classmethod
    def _deux_temps(cls, value: dict) -> dict:
        for role, temps in value.items():
            if set(temps) != set(Temps):
                raise ValueError(f"{role} : préciser les poids par beau et par mauvais temps")
            if any(not poids for poids in temps.values()):
                raise ValueError(f"{role} : au moins une zone par temps")
        return value


class MondeConfig(_Section):
    """Éléments fictifs du monde, injectés dans les prompts et le corpus."""

    brand_name: str = Field(min_length=1)
    ingredient: str = Field(min_length=1)
    concurrent: str = Field(min_length=1, description="Marque concurrente fictive (corpus).")
    indice_protection: str = Field(min_length=1)


class PoidsConfig(_Section):
    """Poids du score de sélection (début de l'étape 16, décidé par le binôme le 4 nov.) :
    score = [α·pertinence + β·rétention + γ·importance + δ·nouveauté + ε·proximité
    − λ·L·redondance] × accès. Chaque terme est déjà entre 0 et 1."""

    pertinence: float = Field(default=1.0, ge=0, description="α : ressemblance à la question.")
    retention: float = Field(default=1.0, ge=0, description="β : le souvenir n'est pas effacé.")
    importance: float = Field(default=1.0, ge=0, description="γ : moment marquant.")
    nouveaute: float = Field(default=1.0, ge=0, description="δ : pas montré récemment.")
    proximite: float = Field(default=0.5, ge=0, description="ε : mémoire collective seulement.")
    redondance: float = Field(default=1.0, ge=0, description="λ : pénalité de la lassitude.")


class ProximiteConfig(_Section):
    """Proximité sociale de l'auteur d'un fait public (mémoire collective) : on écoute davantage
    ses proches."""

    proche: float = Field(default=1.0, ge=0, le=1)
    abonnement: float = Field(default=0.7, ge=0, le=1)
    zone: float = Field(default=0.4, ge=0, le=1)
    tous: float = Field(default=0.4, ge=0, le=1, description="Fait public pour toute la ville.")


class MemoireConfig(_Section):
    """Mémoire sélective : ce qui entre dans le prompt (début de l'étape 16, voir `rag.selection`).

    Filtres durs d'abord (date, rôle, auteur, portée sociale, déjà dans la perception), puis
    `candidats` par source, notés et triés, sans doublons. La rétention d'un souvenir décroît avec
    son âge depuis son dernier rappel ; un souvenir marquant s'efface moins vite."""

    candidats: PositiveInt = Field(
        default=10, description="Candidats retenus par source avant d'être notés."
    )
    demi_vie_jours: PositiveFloat = Field(
        default=7, description="Âge (jours) auquel la rétention d'un souvenir banal vaut 0,5."
    )
    kappa: float = Field(
        default=1.0,
        ge=0,
        description="Demi-vie effective = demi_vie_jours × (1 + κ·importance) : un souvenir "
        "marquant s'efface moins vite.",
    )
    seuil_retention: float = Field(
        default=0.1,
        ge=0,
        lt=1,
        description="Sous ce seuil, un souvenir n'est plus candidat (il reste en base, F6).",
    )
    nouveaute_jours: PositiveInt = Field(
        default=7, description="Un élément montré depuis moins de ce nombre de jours est déjà vu."
    )
    nouveaute_deja_vu: float = Field(default=0.3, ge=0, le=1)
    doublon: float = Field(
        default=0.92,
        gt=0,
        le=1,
        description="Similarité cosinus au-delà de laquelle un candidat double un élément choisi.",
    )
    poids: PoidsConfig = Field(default_factory=PoidsConfig)
    proximite: ProximiteConfig = Field(default_factory=ProximiteConfig)

    @model_validator(mode="before")
    @classmethod
    def _anciens_poids(cls, data: object) -> object:
        """Manifestes des runs de l'étape 15 ter (27-28 oct.) : poids à plat, renommés depuis."""
        if not isinstance(data, dict) or not any(k.startswith("poids_") for k in data):
            return data
        data = dict(data)
        anciens = {"poids_ressemblance": "pertinence", "poids_recence": "retention"}
        anciens["poids_importance"] = "importance"
        poids = dict(data.get("poids", {}))
        for ancien, nouveau in anciens.items():
            if ancien in data:
                poids[nouveau] = data.pop(ancien)
        data["poids"] = poids
        return data


class RagConfig(_Section):
    actif: bool
    modele_embeddings: str = Field(min_length=1)
    passages: NonNegativeInt
    souvenirs: NonNegativeInt
    collectif: NonNegativeInt = Field(
        default=0, description="Faits publics retrouvés par décision (mémoire collective)."
    )
    memoire: MemoireConfig = Field(default_factory=MemoireConfig)


class RenfortsConfig(_Section):
    """Ce qui remet le produit en tête d'un agent (notoriété A), par exposition du jour."""

    proche: float = Field(default=0.5, ge=0, le=1, description="Message d'un proche.")
    publication: float = Field(
        default=0.3, ge=0, le=1, description="Publication « réseaux » ou propos entendu en zone."
    )
    article: float = Field(
        default=0.4,
        ge=0,
        le=1,
        description="Perturbation qui parle du produit (article, publication virale, polémique, "
        "rupture de stock).",
    )
    porteur_vu: float = Field(default=0.1, ge=0, le=1, description="Par porteur vu autour de lui.")
    porteurs_vus_max: float = Field(
        default=0.3,
        ge=0,
        le=1,
        description="Plafond du renfort des porteurs vus en un jour (décidé le 4 nov.) : voir des "
        "porteurs entretient le souvenir sans le saturer.",
    )
    rayon: float = Field(default=0.2, ge=0, le=1, description="Le produit est en rayon sur place.")


class OubliConfig(_Section):
    """Notoriété A : l'agent pense-t-il encore au produit ? (début de l'étape 16)

    A(j) = A(j−1)·2^(−1/h) + renforts, plafonnée à 1 ; un porteur reste à 1. Sous `seuil`, plus
    de relance ni d'appel LLM ; une exposition qui le fait repasser au-dessus déclenche une
    décision (redécouverte). Inactif : tout le monde connaît le produit, comme avant."""

    actif: bool = False
    connus_jour_1: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description="Part des agents qui connaissent le produit au jour 1 (tirée avec la graine).",
    )
    demi_vie_jours: PositiveFloat = Field(
        default=10, description="Demi-vie moyenne de la notoriété, tirée par agent."
    )
    demi_vie_ecart: float = Field(default=3, ge=0, description="Écart type de cette demi-vie.")
    seuil: float = Field(default=0.2, gt=0, le=1, description="Seuil de découverte.")
    seuil_oubli: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description="Seuil sous lequel un agent qui connaît le produit l'oublie, plus bas que le "
        "seuil de découverte (hystérésis) : sans lui, A clignote autour du seuil et chaque "
        "redécouverte coûte une décision. None : le seuil de découverte.",
    )
    renforts: RenfortsConfig = Field(default_factory=RenfortsConfig)
    habituation: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Vitesse d'habituation aux expositions répétées (rayon, porteurs vus) : un "
        "rayon vu tous les jours devient le décor et ne rappelle plus le produit. 0 : aucune.",
    )

    @model_validator(mode="after")
    def _seuil_oubli_sous_le_seuil(self) -> "OubliConfig":
        if self.seuil_oubli is not None and self.seuil_oubli > self.seuil:
            raise ValueError("seuil_oubli doit être inférieur ou égal au seuil de découverte")
        return self


class LassitudeConfig(_Section):
    """Lassitude L : l'agent connaît le produit mais en a assez (début de l'étape 16).

    L(j) = L(j−1)·(1 − recuperation) + taux·exposition, l'exposition du jour (messages reçus,
    porteurs vus, jours passés à le porter) étant entre 0 et 1. Elle pénalise la redondance dans
    la sélection du RAG, donne une phrase au prompt et, dans C0, fait abandonner plus souvent."""

    actif: bool = False
    taux: float = Field(default=0.15, ge=0, le=1)
    recuperation: float = Field(default=0.1, ge=0, le=1)
    niveaux: tuple[float, float, float] = Field(
        default=(0.3, 0.6, 0.85), description="Seuils des trois phrases de lassitude du prompt."
    )
    abandon_c0: float = Field(
        default=0.3, ge=0, le=1, description="Baseline C0 : abandon d'un porteur avec p = ceci × L."
    )

    @field_validator("niveaux")
    @classmethod
    def _croissants(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if not 0 < value[0] < value[1] < value[2] <= 1:
            raise ValueError("trois seuils croissants entre 0 et 1")
        return value


class VilleConfig(_Section):
    """Ville choisie au lancement (décidé le 10 nov., `sim/ville.py`) : sa taille et son plan
    urbain. Elle remplace le nombre de zones de chaque type (`zones`) ; la ville se peuple
    d'elle-même et sa géographie agit sur qui fréquente quels lieux."""

    taille: Taille = Field(
        default=Taille.AUTO,
        description="petite, moyenne, grande, ou auto : d'après le nombre d'agents.",
    )
    plan: Plan = Field(
        default=Plan.HASARD,
        description="radioconcentrique, hippodamien, etoile, lineaire, ou hasard : tiré avec la "
        "graine du run.",
    )
    demi_distance: PositiveFloat | None = Field(
        default=120.0,
        description="Distance (m) à laquelle un lieu attire deux fois moins les habitants d'un "
        "quartier, dans les routines du matin. None : la distance ne compte pas.",
    )


class Config(_Section):
    run: RunConfig
    simulation: SimulationConfig
    llm: LlmConfig
    agents: AgentsConfig
    meteo: MeteoConfig
    commerce: CommerceConfig
    regles: ReglesConfig
    declencheurs: DeclencheursConfig
    reseau: ReseauConfig
    routines: RoutinesConfig
    rag: RagConfig
    oubli: OubliConfig = Field(default_factory=OubliConfig)
    lassitude: LassitudeConfig = Field(default_factory=LassitudeConfig)
    monde: MondeConfig
    population: dict[Role, NonNegativeInt] = Field(
        description="Nombre d'agents par rôle. Un rôle absent compte zéro agent."
    )
    zones: dict[Zone, Annotated[int, Field(ge=1, le=ZONES_PAR_TYPE_MAX)]] = Field(
        default_factory=dict,
        description="Nombre de zones de chaque type (interface de paramétrage). Un type absent en "
        "compte une ; plusieurs exemplaires sont des lieux distincts (« plage_1 », « plage_2 »).",
    )
    ville: VilleConfig | None = Field(
        default=None,
        description="Taille et plan de la ville (10 nov.), à la place de `zones`. Absente : le "
        "monde d'avant, réglé par `zones`.",
    )

    @model_validator(mode="after")
    def _ville_ou_zones(self) -> "Config":
        if self.ville is None:
            return self
        if self.zones:
            raise ValueError("`ville` et `zones` s'excluent : la ville fixe elle-même ses lieux")
        if self.ville.taille is not Taille.AUTO:
            minimum = AGENTS_PAR_TAILLE[self.ville.taille].minimum
            if self.nb_habitants < minimum:
                raise ValueError(
                    f"une ville {self.ville.taille.value} demande au moins {minimum} agents "
                    f"({self.nb_habitants} demandés) : sinon des lieux restent vides et des "
                    "habitants ne se croisent jamais"
                )
        return self

    @property
    def nb_agents(self) -> int:
        return sum(self.population.values())

    @property
    def nb_habitants(self) -> int:
        """Agents présents dans la ville : tous, sauf la météo."""
        return self.nb_agents - self.population.get(Role.METEO, 0)

    @property
    def plan_ville(self) -> PlanVille | None:
        """Géographie de la ville (taille et plan résolus), ou None pour un monde réglé par
        `zones`."""
        if self.ville is None:
            return None
        return construire_plan(
            taille_effective(self.ville.taille, self.nb_habitants),
            plan_effectif(self.ville.plan, self.run.seed),
        )

    @property
    def lieux(self) -> tuple[str, ...]:
        if self.ville is not None:
            return lieux_de_la_ville(taille_effective(self.ville.taille, self.nb_habitants))
        return lieux_du_monde(self.zones)


class ConfigError(Exception):
    """Configuration illisible ou invalide."""


def _fusionner(base: dict, ajouts: dict) -> dict:
    """Fusion récursive : les sections se complètent, une valeur simple remplace celle de la base.
    La population est toujours remplacée en entier : une variante dit qui est là, pas qui part."""
    resultat = dict(base)
    for cle, valeur in ajouts.items():
        if isinstance(valeur, dict) and isinstance(base.get(cle), dict) and cle != "population":
            resultat[cle] = _fusionner(base[cle], valeur)
        else:
            resultat[cle] = valeur
    return resultat


def _lire(path: Path, deja: tuple[Path, ...] = ()) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path} : lecture impossible ({exc})") from exc
    if not isinstance(raw, dict) or "herite_de" not in raw:
        return raw
    base = (path.parent / raw.pop("herite_de")).resolve()
    if base in deja:
        raise ConfigError(f"{path} : héritage circulaire ({base.name})")
    return _fusionner(_lire(base, (*deja, path.resolve())), raw)


def load_config(path: str | Path) -> Config:
    """Lit et valide un fichier de configuration YAML. Une configuration peut déclarer
    `herite_de: autre.yaml` et ne préciser que ce qui change (voir `configs/dev.yaml`)."""
    path = Path(path)
    raw = _lire(path)

    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path} : configuration invalide\n{exc}") from exc

    if config.nb_agents == 0:
        raise ConfigError(f"{path} : la population ne contient aucun agent")
    return config
