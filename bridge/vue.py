"""Vue du monde tenue par le pont à partir du seul flux d'événements, en direct comme en rejeu.

Elle sert l'inspection d'un agent (D2) : historique de ses décisions et des messages qu'il a
envoyés ou reçus en direct. En direct, l'état complet (seuil, rôle, messages reçus de la zone ou
des réseaux) vient en plus de Redis ; en rejeu, cette vue est la seule source.
"""

from collections import deque
from dataclasses import dataclass, field

from bus import events as ev

HISTORIQUE_MAX = 10


@dataclass
class FicheAgent:
    agent_id: str
    zone: str | None = None
    sens: str | None = None
    porteur: bool = False
    palier: str | None = None
    decisions: deque[dict] = field(default_factory=lambda: deque(maxlen=HISTORIQUE_MAX))
    messages_envoyes: deque[dict] = field(default_factory=lambda: deque(maxlen=HISTORIQUE_MAX))
    messages_directs_recus: deque[dict] = field(
        default_factory=lambda: deque(maxlen=HISTORIQUE_MAX)
    )

    def resume(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "zone": self.zone,
            "sens": self.sens,
            "porteur": self.porteur,
            "palier": self.palier,
        }

    def detail(self) -> dict:
        return {
            **self.resume(),
            "derniere_decision": self.decisions[-1] if self.decisions else None,
            "decisions": list(self.decisions),
            "messages_envoyes": list(self.messages_envoyes),
            "messages_directs_recus": list(self.messages_directs_recus),
        }


class VueMonde:
    def __init__(self) -> None:
        self.reinitialiser(None)

    def reinitialiser(self, run_id: str | None) -> None:
        self.run_id = run_id
        self.jour = 0
        self.meteo: dict | None = None
        self.agents: dict[str, FicheAgent] = {}
        self.perturbations: deque[dict] = deque(maxlen=HISTORIQUE_MAX)

    def _fiche(self, agent_id: str) -> FicheAgent:
        if agent_id not in self.agents:
            self.agents[agent_id] = FicheAgent(agent_id)
        return self.agents[agent_id]

    def appliquer(self, event: ev.Envelope) -> None:
        match event:
            case ev.ClockStep():
                self.jour = event.payload.jour
            case ev.Weather():
                self.meteo = event.payload.model_dump(mode="json")
            case ev.AgentDecision():
                f = self._fiche(event.source_agent_id)
                p = event.payload
                f.zone = event.zone.value if event.zone else f.zone
                f.sens, f.porteur = p.sens.value, p.porteur
                f.palier = p.palier.value if p.palier else "regles"
                f.decisions.append(
                    {
                        "jour": self.jour,
                        "event_id": str(event.event_id),
                        "sens": p.sens.value,
                        "action": p.action.value,
                        "porteur": p.porteur,
                        "moteur": p.moteur.value,
                        "justification": p.justification,
                        "appel_llm": p.appel_llm.model_dump(mode="json") if p.appel_llm else None,
                        "caused_by": [str(c) for c in event.caused_by],
                    }
                )
            case ev.AgentMove():
                self._fiche(event.source_agent_id).zone = event.payload.vers.value
            case ev.AgentMessage():
                p = event.payload
                message = {
                    "jour": self.jour,
                    "event_id": str(event.event_id),
                    "de": event.source_agent_id,
                    "canal": p.canal.value,
                    "destinataires": list(p.destinataires),
                    "contenu": p.contenu,
                    "livraison": p.livraison,
                }
                self._fiche(event.source_agent_id).messages_envoyes.append(message)
                for destinataire in p.destinataires:
                    self._fiche(destinataire).messages_directs_recus.append(message)
            case ev.Disturbance():
                self.perturbations.append(
                    {
                        "jour": self.jour,
                        "nature": event.payload.nature.value,
                        "zone": event.zone.value if event.zone else None,
                        "description": event.payload.description,
                    }
                )

    def instantane(self) -> dict:
        return {
            "run_id": self.run_id,
            "jour": self.jour,
            "meteo": self.meteo,
            "agents": [f.resume() for f in sorted(self.agents.values(), key=lambda f: f.agent_id)],
            "perturbations": list(self.perturbations),
        }
