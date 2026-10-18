"""Construction de la population d'un run à partir de la configuration (C2).

Chaque rôle de `config.population` donne ses agents : les rôles LLM sont tenus par `AgentLLM`,
les rôles à règles par un agent à seuil provisoire (les vrais agents à règles arrivent à
l'étape 13), la météo par un acteur du monde. La population complète (abonnés, routines de
déplacement) sera construite à l'étape 11.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass

from agents.agent_llm import AgentLLM
from agents.base import Agent, WorldActor
from agents.factices import MeteoFactice
from agents.regles import AgentSeuil, tirer_seuil
from agents.roles import Role, palier_du_role
from llm.routeur import Routeur
from sim.config import Config
from sim.domaine import Zone
from sim.run import agent_rng

CONTACTS_MAX = 5


@dataclass(frozen=True)
class Identite:
    agent_id: str
    role: Role
    rng: random.Random
    zone: Zone
    contacts: list[str]


def identites(config: Config) -> Iterator[Identite]:
    """Identifiant, générateur, zone de départ et contacts de chaque agent (hors météo).

    Tous les tirages passent par le générateur propre à l'agent, dans un ordre fixe : zone,
    puis contacts. Le reste de ses tirages (seuil...) vient après.
    """
    ids = [
        f"{role.value}_{i:02d}"
        for role, n in config.population.items()
        if role is not Role.METEO
        for i in range(1, n + 1)
    ]
    for role, n in config.population.items():
        if role is Role.METEO:
            continue
        for i in range(1, n + 1):
            agent_id = f"{role.value}_{i:02d}"
            rng = agent_rng(config.run.seed, agent_id)
            autres = [x for x in ids if x != agent_id]
            zone = rng.choice(list(Zone))
            contacts = rng.sample(autres, min(CONTACTS_MAX, len(autres)))
            yield Identite(agent_id, role, rng, zone, contacts)


def acteurs_monde(config: Config) -> list[WorldActor]:
    if config.population.get(Role.METEO, 0):
        return [MeteoFactice(agent_rng(config.run.seed, "meteo"))]
    return []


def construire_population(config: Config, routeur: Routeur) -> tuple[list[Agent], list[WorldActor]]:
    agents: list[Agent] = []
    loi = config.agents.seuil_adoption
    for x in identites(config):
        seuil = tirer_seuil(x.rng, loi)
        if palier_du_role(x.role) is None:
            agents.append(AgentSeuil(x.agent_id, x.role, x.zone, x.rng, seuil))
        else:
            agents.append(
                AgentLLM(
                    x.agent_id,
                    x.role,
                    x.zone,
                    x.rng,
                    routeur,
                    config.monde,
                    config.run.seed,
                    x.contacts,
                    seuil,
                )  # fmt: skip
            )
    return agents, acteurs_monde(config)
