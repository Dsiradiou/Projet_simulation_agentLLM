"""Routines de déplacement (étape 11) : où chaque agent passe sa journée, selon rôle et temps.

Règle sans LLM, lue dans la configuration (`routines`). Chaque matin, un agent reste dans sa zone
avec la probabilité `stabilite`, sinon il tire un type de zone selon les poids de son rôle par
beau ou mauvais temps, et gagne son lieu habituel de ce type (`Agent.lieu_de`). Ces déplacements
exposent chacun aux porteurs d'autres zones (déclencheur d'exposition) et font vivre la scène.
Chaque agent a son propre générateur, dérivé de la graine du run : les tirages ne dépendent ni de
l'ordre des agents ni des autres composants.

Ville (10 nov.) : un lieu éloigné du quartier de l'agent attire moins (atténuation par la distance,
comme dans les modèles gravitaires de la géographie urbaine) : le poids d'un type de zone est
multiplié par `2^(−d / demi_distance)`, d étant la distance entre le quartier et le lieu de ce type
qu'il fréquente. Le plan de la ville change ainsi les habitudes : dans une ville linéaire, les
quartiers des extrémités vont moins au centre. Mêmes tirages, en nombre et en ordre, avec ou sans.
"""

import math
import random
from collections.abc import Mapping

from agents.base import Agent
from agents.roles import Role
from bus.events import WeatherPayload
from sim.config import Config, RoutinesConfig, Temps
from sim.domaine import Zone
from sim.ville import PlanVille

# Heure simulée des déplacements du matin : après la météo (00:00), avant les décisions (10:00).
HEURE_DEPLACEMENT = 8 * 60

TEMPS_DE = {"ensoleille": Temps.BEAU, "canicule": Temps.BEAU, "nuageux": Temps.MAUVAIS}


def temps_de(meteo: WeatherPayload | None) -> Temps:
    if meteo is None:
        return Temps.BEAU
    return TEMPS_DE.get(meteo.conditions, Temps.MAUVAIS)


def tirer_zone(
    config: RoutinesConfig,
    role: Role,
    temps: Temps,
    rng: random.Random,
    attraits: Mapping[Zone, float] | None = None,
) -> Zone | None:
    poids = config.roles.get(role)
    if poids is None:
        return None
    zones = list(poids[temps])
    facteurs = attraits or {}
    return rng.choices(zones, weights=[poids[temps][z] * facteurs.get(z, 1.0) for z in zones])[0]


def attraits_par_quartier(ville: PlanVille, demi_distance: float) -> dict[str, dict[Zone, float]]:
    """Attrait de chaque type de lieu pour les habitants de chaque quartier, entre 0 et 1."""
    pos = ville.positions
    return {
        quartier: {
            zone: 2 ** (-math.dist(pos[quartier], pos[lieu]) / demi_distance)
            for zone, lieu in frequentes.items()
        }
        for quartier, frequentes in ville.frequentes.items()
    }


class Routines:
    def __init__(
        self,
        config: RoutinesConfig,
        seed: int,
        ville: PlanVille | None = None,
        demi_distance: float | None = None,
    ) -> None:
        self.config = config
        self.seed = seed
        self._rngs: dict[str, random.Random] = {}
        self._attraits = (
            attraits_par_quartier(ville, demi_distance) if ville and demi_distance else {}
        )

    @classmethod
    def du_monde(cls, config: "Config") -> "Routines":
        """Routines d'un run : avec la ville et son atténuation par la distance s'il y en a une."""
        demi = config.ville.demi_distance if config.ville else None
        return cls(config.routines, config.run.seed, config.plan_ville, demi)

    def _rng(self, agent_id: str) -> random.Random:
        from sim.run import agent_rng  # import local : sim.run importe la configuration

        if agent_id not in self._rngs:
            self._rngs[agent_id] = agent_rng(self.seed, f"{agent_id}:routine")
        return self._rngs[agent_id]

    def deplacements(self, agents: list[Agent], meteo: WeatherPayload | None) -> dict[str, str]:
        """Lieu du jour des agents qui changent de zone ce matin."""
        temps = temps_de(meteo)
        mouvements: dict[str, str] = {}
        for agent in sorted(agents, key=lambda a: a.agent_id):
            if agent.role not in self.config.roles:
                continue
            rng = self._rng(agent.agent_id)
            reste = rng.random() < self.config.stabilite  # deux tirages par jour, toujours
            attraits = self._attraits.get(agent.lieu_de(Zone.DOMICILE))
            zone = tirer_zone(self.config, agent.role, temps, rng, attraits)
            lieu = agent.lieu_de(zone) if zone is not None else None
            if not reste and lieu is not None and lieu != agent.zone:
                mouvements[agent.agent_id] = lieu
        return mouvements
