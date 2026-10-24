"""Chargement et validation de la configuration d'un run (fichiers `configs/*.yaml`).

Tout paramètre de simulation passe par ce module : rien n'est écrit en dur dans le code (C2).
Une configuration invalide arrête le lancement avec un message qui indique le champ fautif.
"""

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import (
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
from sim.domaine import Canal, Zone


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
    duree_flacon_jours: PositiveInt


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
    lassitude_jours: PositiveInt


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


class RagConfig(_Section):
    actif: bool
    modele_embeddings: str = Field(min_length=1)
    passages: NonNegativeInt
    souvenirs: NonNegativeInt


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
    monde: MondeConfig
    population: dict[Role, NonNegativeInt] = Field(
        description="Nombre d'agents par rôle. Un rôle absent compte zéro agent."
    )

    @property
    def nb_agents(self) -> int:
        return sum(self.population.values())


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
