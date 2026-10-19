"""Météo saisonnière à règles (étape 13), en remplacement de la météo factice (constat, étape 7).

Chaque jour : le temps (beau ou mauvais) suit une chaîne de Markov, la température suit la
saison (montée en début d'été, puis plateau ; pas de rentrée, décision du 19 oct.) avec un écart
quotidien qui garde une part de celui de la veille. Les conditions et l'indice UV en découlent et
restent cohérents : une canicule est chaude et à fort UV, un jour de pluie à UV faible.

Les perturbations ont un effet réel : une vague de chaleur impose plusieurs jours de canicule, un
orage un jour de pluie. Le bulletin du jour cite alors la perturbation qui l'explique (F6).
Le nombre de tirages par jour est fixe : la météo d'une graine est la même dans toutes les
configurations (C0, C1…) tant que les scénarios sont les mêmes.
"""

import math
import random
from uuid import UUID

from agents.base import WorldActor
from bus.events import Disturbance, Envelope, Perturbation, Weather, WeatherPayload
from sim.config import MeteoConfig


class MeteoSaison(WorldActor):
    actor_id = "meteo"

    def __init__(self, config: MeteoConfig, rng: random.Random) -> None:
        self.config = config
        self.rng = rng
        self._beau = True
        self._ecart = 0.0
        self._canicule: tuple[int, int, UUID] | None = None  # (du jour, au jour, cause)
        self._orages: dict[int, UUID] = {}

    def temperature_moyenne(self, jour: int) -> float:
        c = self.config
        avancee = min(1.0, (jour - 1) / c.montee_jours)
        return c.temperature_debut + (c.temperature_plateau - c.temperature_debut) * avancee

    def reagir(self, perturbation: Disturbance, jour: int) -> None:
        """Effet mécanique d'une perturbation, à partir du `jour` où elle s'applique."""
        if perturbation.payload.nature is Perturbation.VAGUE_CHALEUR:
            fin = jour + self.config.duree_vague_chaleur - 1
            self._canicule = (jour, fin, perturbation.event_id)
        elif perturbation.payload.nature is Perturbation.ORAGE:
            self._orages[jour] = perturbation.event_id

    def evenements_du_jour(self, jour: int, minute: int, run_id: str) -> list[Envelope]:
        c, rng = self.config, self.rng
        # Tirages fixes, dans un ordre fixe, quels que soient les forçages du jour.
        bascule, bruit, pluie, uv_alea = rng.random(), rng.gauss(0, 1), rng.random(), rng.random()
        self._beau = bascule >= c.beau_vers_mauvais if self._beau else bascule < c.mauvais_vers_beau
        p = c.persistance
        self._ecart = p * self._ecart + math.sqrt(1 - p * p) * c.ecart_jour * bruit
        beau = self._beau
        temperature = self.temperature_moyenne(jour) + self._ecart + (1.0 if beau else -2.5)
        cause: tuple[UUID, ...] = ()
        if jour in self._orages:
            beau, temperature = False, temperature - 3.0
            cause = (self._orages[jour],)
        elif self._canicule and self._canicule[0] <= jour <= self._canicule[1]:
            beau, temperature = True, max(temperature, c.seuil_canicule + 1.5)
            cause = (self._canicule[2],)
        if not beau:
            conditions = "pluie" if jour in self._orages or pluie < 0.35 else "nuageux"
            uv = 2 + round(2 * uv_alea) if conditions == "nuageux" else 1 + round(uv_alea)
        elif temperature >= c.seuil_canicule:
            conditions, uv = "canicule", 9 + round(2 * uv_alea)
        else:
            conditions = "ensoleille"
            uv = max(6, min(9, round(6 + (temperature - 26) * 0.5 + uv_alea)))
        return [
            Weather(
                run_id=run_id,
                sim_time_occurred=minute,
                sim_time_processed=minute,
                source_agent_id=self.actor_id,
                caused_by=cause,
                payload=WeatherPayload(
                    indice_uv=uv,
                    temperature_c=round(max(-10.0, min(50.0, temperature)), 1),
                    conditions=conditions,
                ),
            )
        ]
