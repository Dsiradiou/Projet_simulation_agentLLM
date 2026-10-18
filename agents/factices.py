"""Agents factices : doublures sans LLM pour tester l'orchestrateur (étape 5).

Ils respectent l'interface des vrais agents et imitent leur latence (mesurée à l'étape 3) sans
appeler de modèle. Leur comportement est volontairement simpliste : ils ne servent qu'à faire
circuler des décisions, des messages et des déplacements. Ils seront remplacés par les vrais
agents LLM (`agents.agent_llm`, branchés au run à l'étape 7) et à règles (étape 13).
"""

import asyncio
import random

from agents.base import Agent, Decision, MessageIntent, Perception, WorldActor
from agents.roles import Palier, Role
from bus.events import Envelope, LlmCall, Moteur, Weather, WeatherPayload
from sim.config import Config
from sim.domaine import Action, Canal, Sens, Zone

# Latence médiane d'une décision en séquentiel, mesurée à l'étape 3 (docs/mesures/).
LATENCE_MESUREE_S = {Palier.PETIT: 0.76, Palier.MOYEN: 1.49, Palier.GRAND: 2.14}

_ACTION = {
    Sens.EFFICACE: Action.APPLIQUER,
    Sens.TENDANCE: Action.EN_PARLER,
    Sens.BANAL: Action.IGNORER,
    Sens.DEPASSE: Action.ABANDONNER,
}


class AgentFactice(Agent):
    def __init__(
        self,
        agent_id: str,
        role: Role,
        zone: Zone,
        rng: random.Random,
        contacts: list[str],
        facteur_latence: float = 0.0,
    ) -> None:
        super().__init__(agent_id, role, zone, rng)
        self.contacts = contacts
        self.latence_s = LATENCE_MESUREE_S[self.palier] * facteur_latence if self.palier else 0.0

    async def decider(self, perception: Perception) -> Decision:
        if self.latence_s:
            await asyncio.sleep(self.latence_s)
        rng = self.rng
        sens = self.sens or rng.choice(list(Sens))
        cites = [s for m in perception.messages for s in Sens if s.value in m.payload.contenu]
        if cites and rng.random() < 0.5:
            sens = rng.choice(cites)  # influence des messages reçus
        elif rng.random() < 0.1:
            sens = rng.choice(list(Sens))

        messages: tuple[MessageIntent, ...] = ()
        if rng.random() < 0.3:
            canal = rng.choice(list(Canal))
            destinataires = (rng.choice(self.contacts),) if canal is Canal.DIRECT else ()
            if canal is Canal.DIRECT and not self.contacts:
                canal, destinataires = Canal.ZONE, ()
            messages = (
                MessageIntent(canal, f"Pour moi, ce produit est {sens.value}.", destinataires),
            )

        return Decision(
            action=_ACTION[sens],
            sens=sens,
            justification="Décision factice (étape 5), sans appel de modèle.",
            porteur=sens in (Sens.EFFICACE, Sens.TENDANCE),
            moteur=Moteur.LLM if self.palier else Moteur.REGLE,
            palier=self.palier,
            appel_llm=(
                LlmCall(
                    modele=f"factice-{self.palier}",
                    tokens_entree=0,
                    tokens_sortie=0,
                    duree_s=self.latence_s,
                )
                if self.palier
                else None
            ),  # fmt: skip
            messages=messages,
            deplacement=rng.choice(list(Zone)) if rng.random() < 0.3 else None,
            caused_by=tuple(m.event_id for m in perception.messages)
            + tuple(e.event_id for e in perception.evenements),
        )


class MeteoFactice(WorldActor):
    actor_id = "meteo"

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def evenements_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]:
        conditions = self.rng.choice(["ensoleille", "ensoleille", "nuageux", "canicule"])
        return [
            Weather(
                run_id=run_id,
                sim_time_occurred=minute,
                sim_time_processed=minute,
                source_agent_id=self.actor_id,
                payload=WeatherPayload(
                    indice_uv=self.rng.randint(3, 10),
                    temperature_c=round(self.rng.uniform(24, 38), 1),
                    conditions=conditions,
                ),
            )
        ]


def population_factice(
    config: Config, facteur_latence: float = 0.0
) -> tuple[list[Agent], list[WorldActor]]:
    """Construit la population décrite par la configuration avec des agents factices."""
    from agents.population import acteurs_monde, equiper, identites  # import local : cycle

    agents: list[Agent] = [
        equiper(
            AgentFactice(x.agent_id, x.role, x.zone, x.rng, x.contacts, facteur_latence), x, config
        )
        for x in identites(config)
    ]
    return agents, acteurs_monde(config)
