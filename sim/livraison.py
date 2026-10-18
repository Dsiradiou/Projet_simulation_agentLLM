"""Boîte aux lettres : un message n'est remis à son destinataire qu'une fois livré (C3, C6).

Un message émis à `sim_time_processed` est lisible à partir de `payload.livraison`. En mode
synchrone, s'y ajoute la barrière du pas : un agent ne lit jamais un message émis le jour même,
puisque tous les agents d'un même jour décident en même temps.
"""

from collections import defaultdict

from bus.events import AgentMessage


class Boite:
    def __init__(self) -> None:
        self._en_attente: dict[str, list[AgentMessage]] = defaultdict(list)
        # Pour chaque agent, moment de livraison de chaque message déjà remis (contrôle C6).
        self._remis: dict[str, dict] = defaultdict(dict)

    def deposer(self, message: AgentMessage, destinataires: list[str]) -> None:
        for agent_id in destinataires:
            if agent_id != message.source_agent_id:
                self._en_attente[agent_id].append(message)

    def relever(self, agent_id: str, minute: int, barriere: int) -> tuple[AgentMessage, ...]:
        """Retire et renvoie les messages lisibles à `minute`, émis avant `barriere`."""
        prets, restants = [], []
        for message in self._en_attente.get(agent_id, []):
            lisible = message.payload.livraison <= minute and message.sim_time_processed < barriere
            (prets if lisible else restants).append(message)
        self._en_attente[agent_id] = restants
        # Tri stable : à livraison égale, l'ordre de dépôt (déterministe) est conservé. Ne jamais
        # départager par event_id, tiré au hasard : deux runs de même graine divergeraient.
        prets.sort(key=lambda m: (m.payload.livraison, m.sim_time_processed))
        for message in prets:
            self._remis[agent_id][message.event_id] = message.payload.livraison
        return tuple(prets)

    def remis_avant(self, agent_id: str, event_id: object, minute: int) -> bool:
        """Vrai si ce message a été remis à l'agent et était livré à `minute`."""
        livraison = self._remis.get(agent_id, {}).get(event_id)
        return livraison is not None and livraison <= minute

    def en_attente(self, agent_id: str) -> int:
        return len(self._en_attente.get(agent_id, []))
