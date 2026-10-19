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
from agents.regles import AgentAuSeuil, AgentConcurrent, AgentRegleC0, tirer_seuil
from agents.roles import Role, palier_du_role
from llm.routeur import Routeur
from rag.corpus import lire_corpus
from sim.calendrier import CalendrierConcurrent
from sim.commerce import Commerce
from sim.config import Config, MoteurAgents, Temps
from sim.domaine import Zone
from sim.meteo import MeteoSaison
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
    """Acteurs à règles du monde (étape 13) : météo saisonnière, calendrier du concurrent."""
    acteurs: list[WorldActor] = []
    if config.population.get(Role.METEO, 0):
        acteurs.append(MeteoSaison(config.meteo, agent_rng(config.run.seed, "meteo")))
    if config.population.get(Role.MARQUE_CONCURRENTE, 0):
        acteurs.append(CalendrierConcurrent(config.monde))
    return acteurs


def construire_commerce(config: Config, agents: list[Agent]) -> Commerce:
    """Stocks des points de vente, à l'échelle de la population ; un vendeur par point de vente
    signe ses réassorts."""
    vendeurs = sorted(a.agent_id for a in agents if a.role is Role.VENDEUR)
    points = config.commerce.points_de_vente
    signataires = {zone: vendeurs[i] for i, zone in enumerate(points) if i < len(vendeurs)}
    return Commerce(config.commerce, len(agents), signataires)


def jours_de_campagne(config: Config) -> set[int]:
    """Jours où la marque communique (corpus) : l'influence externe de la baseline (Bass p)
    y est plus forte, comme les agents LLM y lisent un nouveau communiqué."""
    return {
        d.jour
        for d in lire_corpus(config.monde)
        if d.type == "communique" and d.jour >= 1 and d.auteur == config.monde.brand_name
    }


def equiper(agent: Agent, identite: Identite, config: Config) -> Agent:
    """Place l'agent dans le réseau social et lui donne ses déclencheurs."""
    agent.abonnements = identite.abonnements
    agent.declencheurs = config.declencheurs
    return agent


def construire_population(
    config: Config, routeur: Routeur | None, recuperateur: "Recuperateur | None" = None
) -> tuple[list[Agent], list[WorldActor]]:
    """Population du run. `routeur` n'est requis que si des rôles sont tenus par des LLM."""
    agents: list[Agent] = []
    loi = config.agents.seuil_adoption
    regles = config.regles
    campagne = jours_de_campagne(config)
    for x in identites(config):
        seuil = tirer_seuil(x.rng, loi)
        if x.role is Role.MARQUE_CONCURRENTE:
            agent: Agent = AgentConcurrent(x.agent_id, x.role, x.zone, x.rng, config.monde)
        elif x.role is Role.FIDELE_AUTRE_MARQUE:
            bonus = regles.bonus_fidelite
            agent = AgentAuSeuil(x.agent_id, x.role, x.zone, x.rng, seuil, regles, bonus)
        elif palier_du_role(x.role) is None:  # vendeurs
            agent = AgentAuSeuil(x.agent_id, x.role, x.zone, x.rng, seuil, regles)
        elif config.agents.moteur is MoteurAgents.REGLES:  # baseline C0 (E1)
            agent = AgentRegleC0(
                x.agent_id, x.role, x.zone, x.rng, seuil, regles, config.monde, x.contacts,
                campagne, config.agents.messages_par_semaine.get(x.role, 0),
            )  # fmt: skip
        else:
            if routeur is None:
                raise ValueError("agents LLM demandés sans routeur de modèles")
            agent = AgentLLM(
                x.agent_id, x.role, x.zone, x.rng, routeur, config.monde,
                config.run.seed, x.contacts, seuil,
                messages_par_semaine=config.agents.messages_par_semaine.get(x.role, 0),
                recuperateur=recuperateur,
            )  # fmt: skip
        agents.append(equiper(agent, x, config))
    return agents, acteurs_monde(config)
