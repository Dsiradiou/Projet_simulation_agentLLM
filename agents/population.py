"""Construction de la population d'un run à partir de la configuration (C2, étape 11).

Chaque rôle de `config.population` donne ses agents : les rôles LLM sont tenus par `AgentLLM`,
les rôles à règles par un agent à seuil (les vrais agents à règles arrivent à l'étape 13), la météo
par un acteur du monde. Chaque agent reçoit aussi sa place dans le réseau social :
- ses **abonnements** : les comptes dont il reçoit les publications « réseaux », tirés selon
  l'audience de chaque rôle (`reseau.audience`) — les créateurs de contenu sont très suivis ;
- ses **contacts directs** : un petit cercle de proches, en partie réciproque ;
- sa **zone de départ**, tirée dans sa routine de beau temps (`routines`).
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.agent_llm import AgentLLM
from agents.base import Agent, WorldActor
from agents.factices import MeteoFactice
from agents.regles import AgentSeuil, tirer_seuil
from agents.roles import Role, palier_du_role
from llm.routeur import Routeur
from sim.config import Config, Temps
from sim.domaine import Zone
from sim.routines import tirer_zone
from sim.run import agent_rng

if TYPE_CHECKING:
    from rag.recuperation import Recuperateur


@dataclass(frozen=True)
class Identite:
    agent_id: str
    role: Role
    rng: random.Random
    zone: Zone
    contacts: list[str]
    abonnements: tuple[str, ...]


def _tirage_pondere(
    rng: random.Random, candidats: list[str], poids: list[float], k: int
) -> list[str]:
    """k candidats distincts, chacun tiré avec une probabilité proportionnelle à son poids."""
    candidats, poids = list(candidats), list(poids)
    choisis: list[str] = []
    for _ in range(min(k, len(candidats))):
        i = rng.choices(range(len(candidats)), weights=poids)[0]
        choisis.append(candidats.pop(i))
        poids.pop(i)
    return choisis


def identites(config: Config) -> Iterator[Identite]:
    """Identité de chaque agent (hors météo), dans un ordre et avec des tirages reproductibles.

    Tous les tirages d'un agent passent par son propre générateur, dans un ordre fixe : zone,
    contacts, abonnements. La suite de ses tirages (seuil...) vient après.
    """
    roles = {
        f"{role.value}_{i:02d}": role
        for role, n in config.population.items()
        if role is not Role.METEO
        for i in range(1, n + 1)
    }
    ids = list(roles)
    audience = config.reseau.audience
    tirages: dict[str, tuple[random.Random, Zone, list[str], list[str]]] = {}
    for agent_id, role in roles.items():
        rng = agent_rng(config.run.seed, agent_id)
        autres = [x for x in ids if x != agent_id]
        zone = tirer_zone(config.routines, role, Temps.BEAU, rng) or rng.choice(list(Zone))
        contacts = rng.sample(autres, min(config.reseau.contacts_directs, len(autres)))
        poids = [audience.get(roles[x], 1.0) for x in autres]
        suivis = _tirage_pondere(rng, autres, poids, config.reseau.abonnements_par_agent)
        tirages[agent_id] = (rng, zone, contacts, suivis)
    # Réciprocité partielle : une fois sur deux, le proche choisi nous compte aussi parmi les siens.
    reciproques: dict[str, list[str]] = {x: [] for x in ids}
    for agent_id in ids:
        rng_lien = agent_rng(config.run.seed, f"{agent_id}:liens")
        for contact in tirages[agent_id][2]:
            if rng_lien.random() < 0.5 and agent_id not in tirages[contact][2]:
                reciproques[contact].append(agent_id)
    for agent_id, role in roles.items():
        rng, zone, contacts, suivis = tirages[agent_id]
        yield Identite(
            agent_id, role, rng, zone, contacts + reciproques[agent_id], tuple(sorted(suivis))
        )


def acteurs_monde(config: Config) -> list[WorldActor]:
    if config.population.get(Role.METEO, 0):
        return [MeteoFactice(agent_rng(config.run.seed, "meteo"))]
    return []


def equiper(agent: Agent, identite: Identite, config: Config) -> Agent:
    """Place l'agent dans le réseau social et lui donne ses déclencheurs."""
    agent.abonnements = identite.abonnements
    agent.declencheurs = config.declencheurs
    return agent


def construire_population(
    config: Config, routeur: Routeur, recuperateur: "Recuperateur | None" = None
) -> tuple[list[Agent], list[WorldActor]]:
    agents: list[Agent] = []
    loi = config.agents.seuil_adoption
    for x in identites(config):
        seuil = tirer_seuil(x.rng, loi)
        if palier_du_role(x.role) is None:
            agent: Agent = AgentSeuil(x.agent_id, x.role, x.zone, x.rng, seuil)
        else:
            agent = AgentLLM(
                x.agent_id, x.role, x.zone, x.rng, routeur, config.monde,
                config.run.seed, x.contacts, seuil,
                messages_par_semaine=config.agents.messages_par_semaine.get(x.role, 0),
                recuperateur=recuperateur,
            )  # fmt: skip
        agents.append(equiper(agent, x, config))
    return agents, acteurs_monde(config)
