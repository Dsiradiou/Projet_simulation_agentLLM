"""Routines de déplacement (étape 11) : où chaque agent passe sa journée, selon rôle et temps.

Règle sans LLM, lue dans la configuration (`routines`). Chaque matin, un agent reste dans sa zone
avec la probabilité `stabilite`, sinon il tire une zone selon les poids de son rôle par beau ou
mauvais temps. Ces déplacements exposent chacun aux porteurs d'autres zones (déclencheur
d'exposition) et font vivre la scène. Chaque agent a son propre générateur, dérivé de la graine du
run : les tirages ne dépendent ni de l'ordre des agents ni des autres composants.
"""

import random

from agents.base import Agent
from agents.roles import Role
from bus.events import WeatherPayload
from sim.config import RoutinesConfig, Temps
from sim.domaine import Zone

# Heure simulée des déplacements du matin : après la météo (00:00), avant les décisions (10:00).
HEURE_DEPLACEMENT = 8 * 60

TEMPS_DE = {"ensoleille": Temps.BEAU, "canicule": Temps.BEAU, "nuageux": Temps.MAUVAIS}


def temps_de(meteo: WeatherPayload | None) -> Temps:
    if meteo is None:
        return Temps.BEAU
    return TEMPS_DE.get(meteo.conditions, Temps.MAUVAIS)


def tirer_zone(config: RoutinesConfig, role: Role, temps: Temps, rng: random.Random) -> Zone | None:
    poids = config.roles.get(role)
    if poids is None:
        return None
    zones = list(poids[temps])
    return rng.choices(zones, weights=[poids[temps][z] for z in zones])[0]


class Routines:
    def __init__(self, config: RoutinesConfig, seed: int) -> None:
        self.config = config
        self.seed = seed
        self._rngs: dict[str, random.Random] = {}

    def _rng(self, agent_id: str) -> random.Random:
        from sim.run import agent_rng  # import local : sim.run importe la configuration

        if agent_id not in self._rngs:
            self._rngs[agent_id] = agent_rng(self.seed, f"{agent_id}:routine")
        return self._rngs[agent_id]

    def deplacements(self, agents: list[Agent], meteo: WeatherPayload | None) -> dict[str, Zone]:
        """Zone du jour des agents qui changent de zone ce matin."""
        temps = temps_de(meteo)
        mouvements: dict[str, Zone] = {}
        for agent in sorted(agents, key=lambda a: a.agent_id):
            if agent.role not in self.config.roles:
                continue
            rng = self._rng(agent.agent_id)
            reste = rng.random() < self.config.stabilite  # deux tirages par jour, toujours
            zone = tirer_zone(self.config, agent.role, temps, rng)
            if not reste and zone is not None and zone is not agent.zone:
                mouvements[agent.agent_id] = zone
        return mouvements
