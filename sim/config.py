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
)

from agents.roles import Palier, Role
from sim.domaine import Canal


class Mode(StrEnum):
    """Mode d'exécution, choisi au lancement (C4)."""

    SYNCHRONE = "synchrone"
    ASYNCHRONE = "asynchrone"


class _Section(BaseModel):
    # extra="forbid" : une clé mal orthographiée dans le YAML est une erreur, pas un oubli.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RunConfig(_Section):
    seed: NonNegativeInt = Field(description="Graine explicite, pour la reproductibilité.")


class SimulationConfig(_Section):
    duree_jours: PositiveInt = Field(description="Durée simulée, un pas = un jour.")
    mode: Mode
    delais_livraison_min: dict[Canal, NonNegativeInt] = Field(
        description="Délai, en minutes simulées, avant qu'un message soit lisible, par canal."
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


class AgentsConfig(_Section):
    seuil_adoption: SeuilConfig = Field(
        description="Loi du seuil d'adoption individuel (Granovetter), bornée à [0, 1]."
    )


class MondeConfig(_Section):
    """Éléments fictifs du monde, injectés dans les prompts et le corpus."""

    brand_name: str = Field(min_length=1)
    ingredient: str = Field(min_length=1)
    indice_protection: str = Field(min_length=1)


class Config(_Section):
    run: RunConfig
    simulation: SimulationConfig
    llm: LlmConfig
    agents: AgentsConfig
    monde: MondeConfig
    population: dict[Role, NonNegativeInt] = Field(
        description="Nombre d'agents par rôle. Un rôle absent compte zéro agent."
    )

    @property
    def nb_agents(self) -> int:
        return sum(self.population.values())


class ConfigError(Exception):
    """Configuration illisible ou invalide."""


def load_config(path: str | Path) -> Config:
    """Lit et valide un fichier de configuration YAML."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path} : lecture impossible ({exc})") from exc

    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path} : configuration invalide\n{exc}") from exc

    if config.nb_agents == 0:
        raise ConfigError(f"{path} : la population ne contient aucun agent")
    return config
