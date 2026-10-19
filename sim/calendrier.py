"""Calendrier scripté de la marque concurrente (étape 13), cohérent avec le corpus.

Les faits datés du corpus qui concernent le concurrent deviennent des événements du monde : la
promotion du jour 21 au jour 27 au centre commercial, la nouvelle formule annoncée au jour 45.
Ils ont des effets mécaniques (la promotion relève le seuil des fidèles, voir `sim.commerce`) et
sont perçus par les agents présents dans la zone concernée.
"""

from agents.base import WorldActor
from bus.events import Disturbance, DisturbancePayload, Envelope, Perturbation
from sim.config import MondeConfig
from sim.domaine import Zone


class CalendrierConcurrent(WorldActor):
    actor_id = "marque_concurrente_01"

    def __init__(self, monde: MondeConfig) -> None:
        nom = monde.concurrent
        self.dates: dict[int, tuple[Perturbation, Zone | None, str]] = {
            21: (
                Perturbation.PROMOTION_CONCURRENTE,
                Zone.CENTRE_COMMERCIAL,
                f"{nom} est à 8 euros le flacon au centre commercial jusqu'au jour 27.",
            ),
            45: (
                Perturbation.ARTICLE_PRESSE,
                None,
                f"{nom} annonce une nouvelle formule au fini moins épais, en rayon au jour 50.",
            ),
        }

    def evenements_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]:
        if jour not in self.dates:
            return []
        nature, zone, description = self.dates[jour]
        return [
            Disturbance(
                run_id=run_id,
                sim_time_occurred=minute,
                sim_time_processed=minute,
                source_agent_id=self.actor_id,
                zone=zone,
                payload=DisturbancePayload(nature=nature, description=description),
            )
        ]
