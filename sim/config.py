"""Chargement et validation de la configuration d'un run (fichiers `configs/*.yaml`).

Tout paramètre de simulation passe par ce module : rien n'est écrit en dur dans le code (C2).
Une configuration invalide arrête le lancement avec un message qui indique le champ fautif.
"""

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, ValidationError

from agents.roles import Role


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


class MondeConfig(_Section):
    """Éléments fictifs du monde, injectés dans les prompts et le corpus."""

    brand_name: str = Field(min_length=1)
    ingredient: str = Field(min_length=1)
    indice_protection: str = Field(min_length=1)


class Config(_Section):
    run: RunConfig
    simulation: SimulationConfig
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
